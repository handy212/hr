from django.urls import path

from . import views

# Paths match the vendor firmware exactly, including its spellings.
urlpatterns = [
    path("Device/Keepalive", views.KeepaliveView.as_view()),
    path("Device/UploadWorkSetting", views.UploadWorkSettingView.as_view()),
    path("Device/DownloadWorkSetting", views.DownloadWorkSettingView.as_view()),
    path("Device/RemoteCommand", views.RemoteCommandView.as_view()),
    path(
        "Device/RegisterIdentifyTicketResult",
        views.RegisterIdentifyTicketResultView.as_view(),
    ),
    path("Device/UploadSnapshoot", views.UploadSnapshootView.as_view()),
    path("Device/RequestAuthorization", views.RequestAuthorizationView.as_view()),
    path("People/DownloadPeopleList", views.DownloadPeopleListView.as_view()),
    path(
        "People/DownloadPeopleListResult",
        views.DownloadPeopleListResultView.as_view(),
    ),
    path("People/DeletePeopleList", views.DeletePeopleListView.as_view()),
    path(
        "People/DeletePeopleListResult", views.DeletePeopleListResultView.as_view()
    ),
    path("People/PushPeople", views.PushPeopleView.as_view()),
    path("Record/UploadIdentifyRecord", views.UploadIdentifyRecordView.as_view()),
    path("Record/UploadSystemRecord", views.UploadSystemRecordView.as_view()),
]
