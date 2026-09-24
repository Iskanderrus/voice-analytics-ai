"""Create a local demo user and print its API token.

This command is for local examples and smoke tests. A deployed product should use
its normal identity flow instead of provisioning a shared demo credential.
"""

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token


class Command(BaseCommand):
    help = "Create (or reuse) the demo user and print its API token."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--username", default="demo")

    def handle(self, *args: Any, username: str, **options: Any) -> None:
        user, _ = get_user_model().objects.get_or_create(username=username)
        token, _ = Token.objects.get_or_create(user=user)
        self.stdout.write(token.key)
