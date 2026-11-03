"""Cron classifier (design §12.1.16, D12).

Every pfSense ``cron/item`` is classified and left UNSUPPORTED: OPNsense cron
only runs configd actions, so a pfSense shell command cannot be written into
``OPNsense/cron``. Nothing is rendered.
"""

from __future__ import annotations

from ..context import SourceContext
from ..model import IfaceMap, Item, Status
from ..registry import register
from .base import Translator, pf_text

#: pfSense/package internal jobs, checked first (§12.1.16).
_SYSTEM_JOBS = (
    "adjkerntz",
    "rc.periodic",
    "newsyslog",
    "rc.update_bogons",
    "rc.dyndns.update",
    "expiretable",
    "rc.update_urltables",
    "rc.update_pkg_metadata",
)

_WRAP_REASON = (
    "OPNsense cron only runs configd actions; wrap the command in an action under "
    "/usr/local/opnsense/service/conf/actions.d/ and add a cron job"
)


@register
class CronTranslator(Translator):
    section = "cron"
    title = "Cron jobs"
    tier = 1
    order = 150
    base_policy = "none"
    container = None

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for index, job in enumerate(src.pf.findall("cron/item")):
            schedule = " ".join(
                pf_text(job, tag) for tag in ("minute", "hour", "mday", "month", "wday")
            )
            command = pf_text(job, "command")
            label = f"{schedule} {command[:60]}".strip()
            reason, followups = self._classify(command)
            items.append(
                Item(
                    f"cron#{index}",
                    self.section,
                    label,
                    job,
                    Status.UNSUPPORTED,
                    reason=reason,
                    followups=followups,
                )
            )
        return items

    @staticmethod
    def _classify(command: str) -> tuple[str, list[str]]:
        if any(token in command for token in _SYSTEM_JOBS):
            return ("pfSense system job; OPNsense has its own built-in equivalent", [])
        if "pfblockerng" in command:
            return ("pfBlockerNG job; not applicable", [])
        if "acme" in command:
            return ("ACME renewals are handled by os-acme-client", [])
        if "servicewatchdog" in command:
            return ("use Monit (Services \u25b8 Monit)", [])
        return (_WRAP_REASON, [_WRAP_REASON])
