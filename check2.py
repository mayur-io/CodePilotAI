import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev')
os.environ['ANDROID_DEEP_LINK_SCHEME']='codepilotai'
django.setup()
from django.conf import settings
print(settings.ANDROID_DEEP_LINK_SCHEME)
