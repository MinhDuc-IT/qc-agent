from dataclasses import dataclass

from ..domain.models import QCRun


@dataclass(frozen=True)
class PublishReceipt:
    channel: str
    action: str
    dry_run: bool


class JiraPublisher:
    """Dry-run boundary until a reviewed Jira API configuration is supplied."""
    def publish(self, run: QCRun) -> PublishReceipt:
        return PublishReceipt("jira", "would-create-or-dedupe-by-fingerprint", True)


class SlackPublisher:
    """Dry-run boundary; security detail is intentionally never included."""
    def publish(self, run: QCRun) -> PublishReceipt:
        return PublishReceipt("slack", "would-publish-run-summary", True)
