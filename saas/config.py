"""Platform configuration; imported by the project settings integration hook."""


# The platform console deliberately excludes company/employee context and HR scripts.
def platform_template_engine(base_dir):
    return {
        "NAME": "platform",
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [base_dir / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
