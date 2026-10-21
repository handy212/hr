"""
horilla_views/generic/cbv/history.py
"""

from django.apps import apps
from django.contrib import messages
from django.http import Http404, HttpResponseForbidden
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.generic import DetailView
from simple_history.utils import get_history_model_for_model

from horilla.horilla_middlewares import _thread_locals
from horilla.http.response import HorillaRedirect
from horilla_views.cbv_methods import hx_request_required, login_required
from horilla_views.generic.cbv.views import HorillaFormView
from horilla_views.history_methods import get_diff


@method_decorator(login_required, name="dispatch")
@method_decorator(hx_request_required, name="dispatch")
class HorillaHistoryView(DetailView):
    """
    GenericHorillaHistoryView
    """

    template_name = "generic/horilla_history_view.html"
    has_perm_to_revert = False
    fields: list = []
    history_related_name = "history"

    def get_context_data(self, **kwargs):
        """
        Get context data
        """
        context = super().get_context_data(**kwargs)
        instance = self.get_object()
        tracking = None
        log_entries = None
        if self.history_related_name:
            tracking = get_diff(instance, self.history_related_name)
        # Prefer simple-history when it produced entries; otherwise use auditlog.
        if tracking:
            context["tracking"] = tracking
            context["log_entries"] = None
        elif hasattr(instance, "horilla_history"):
            context["tracking"] = None
            context["log_entries"] = instance.horilla_history.all().order_by(
                "-timestamp"
            )
        else:
            context["tracking"] = tracking
            context["log_entries"] = log_entries
        context["model"] = (
            f"{self.model._meta.app_label}.{self.model._meta.object_name}"
        )
        context["has_perm_to_revert"] = self.has_perm_to_revert
        return context

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        request = getattr(_thread_locals, "request", None)
        self.request = request

    def get_queryset(self):
        """Limit history lookups to objects visible in the current company scope."""
        if self.model is not None:
            return self.model.objects.all()
        return super().get_queryset()

    def get(self, request, *args, **kwargs):
        """Resolve and authorize a model before loading its history target."""
        if not self.model:
            model_param = request.GET.get("model")
            if model_param:
                try:
                    app_label, model_name = model_param.split(".", 1)
                    self.model = apps.get_model(app_label, model_name)
                except (LookupError, ValueError) as exc:
                    raise Http404("Invalid history model.") from exc
        if not self.model:
            messages.error(request, _("Invalid model parameter format."))
            return HorillaFormView.HttpResponse()

        opts = self.model._meta
        if not request.user.has_perm(f"{opts.app_label}.view_{opts.model_name}"):
            return HttpResponseForbidden()

        if hasattr(self.model, "history") and hasattr(
            getattr(self.model, "history"), "model"
        ):
            self.history_related_name = "history"
        elif hasattr(self.model, "history_set"):
            self.history_related_name = "history_set"
        elif hasattr(self.model, "history"):
            self.history_related_name = "history"
        else:
            self.history_related_name = None
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Revert a history entry only for an authorized, in-scope object."""
        history_id = kwargs.get("history_id")
        model_param = request.GET.get("model")
        if not model_param:
            messages.error(request, _("Missing model parameter."))
            return HorillaRedirect(request)
        try:
            app_label, model_name = model_param.split(".", 1)
            self.model = apps.get_model(app_label, model_name)
        except (LookupError, ValueError) as exc:
            raise Http404("Invalid history model.") from exc

        opts = self.model._meta
        if not request.user.has_perm(f"{opts.app_label}.change_{opts.model_name}"):
            return HttpResponseForbidden()

        instance = self.get_object()
        history = (
            get_history_model_for_model(self.model)
            .objects.filter(history_id=history_id)
            .first()
        )
        if not history or history.instance.pk != instance.pk:
            messages.error(request, _("History record not found."))
            return HorillaRedirect(request)
        history.instance.save()
        messages.success(request, _("History reverted"))

        return HorillaFormView.HttpResponse()
