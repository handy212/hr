"""Small upstream template hook; all entitlement policy remains in SaaS."""

from django import template
from saas.features import request_feature_allowed

register = template.Library()
register.filter("has_plan_feature", request_feature_allowed)
