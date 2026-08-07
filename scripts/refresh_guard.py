from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ScheduleSlot:
    target: time
    phase: str
    max_delay_minutes: int


SCHEDULE_SLOTS = {
    # Primary runs start seven minutes before the requested update time so the
    # roughly six-minute data build normally finishes on time. Backups avoid
    # the top-of-hour GitHub Actions traffic peak.
    "50 0 * * 1-5": ScheduleSlot(time(8, 50), "preopen_watch", 45),
    "8 1 * * 1-5": ScheduleSlot(time(8, 50), "preopen_watch", 45),
    "53 1 * * 1-5": ScheduleSlot(time(10, 0), "morning_entry", 70),
    "11 2 * * 1-5": ScheduleSlot(time(10, 0), "morning_entry", 70),
    "13 3 * * 1-5": ScheduleSlot(time(11, 20), "morning_entry", 50),
    "31 3 * * 1-5": ScheduleSlot(time(11, 20), "morning_entry", 50),
    "23 5 * * 1-5": ScheduleSlot(time(13, 30), "morning_entry", 50),
    "41 5 * * 1-5": ScheduleSlot(time(13, 30), "morning_entry", 50),
    "23 6 * * 1-5": ScheduleSlot(time(14, 30), "afternoon_risk", 50),
    "41 6 * * 1-5": ScheduleSlot(time(14, 30), "afternoon_risk", 50),
    "53 11 * * 1-5": ScheduleSlot(time(20, 0), "evening_watch", 180),
    "11 12 * * 1-5": ScheduleSlot(time(20, 0), "evening_watch", 180),
    "10 15 * * 1-5": ScheduleSlot(time(23, 10), "overnight_watch", 60),
    "28 15 * * 1-5": ScheduleSlot(time(23, 10), "overnight_watch", 60),
}
WATCHDOG_SCHEDULE = "7,22,37,52 1-12 * * 1-5"
PRIMARY_SLOTS = (
    ("50 0 * * 1-5", SCHEDULE_SLOTS["50 0 * * 1-5"]),
    ("53 1 * * 1-5", SCHEDULE_SLOTS["53 1 * * 1-5"]),
    ("13 3 * * 1-5", SCHEDULE_SLOTS["13 3 * * 1-5"]),
    ("23 5 * * 1-5", SCHEDULE_SLOTS["23 5 * * 1-5"]),
    ("23 6 * * 1-5", SCHEDULE_SLOTS["23 6 * * 1-5"]),
    ("53 11 * * 1-5", SCHEDULE_SLOTS["53 11 * * 1-5"]),
    ("10 15 * * 1-5", SCHEDULE_SLOTS["10 15 * * 1-5"]),
)


@dataclass(frozen=True)
class RefreshDecision:
    run_generator: bool
    deploy_required: bool
    effective_schedule: str
    reason: str


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def decide_watchdog(latest: dict[str, Any] | None, now: datetime | None = None) -> RefreshDecision:
    local_now = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    due_slots: list[tuple[str, ScheduleSlot, datetime]] = []
    for schedule, slot in PRIMARY_SLOTS:
        target = datetime.combine(local_now.date(), slot.target, tzinfo=CN_TZ)
        if local_now >= target + timedelta(minutes=20):
            due_slots.append((schedule, slot, target))

    if not due_slots:
        return RefreshDecision(False, False, "", "No target snapshot is due yet.")

    schedule, slot, target = due_slots[-1]
    latest = latest or {}
    latest_generated = parse_datetime(latest.get("generated_at"))
    latest_phase = str(latest.get("update_phase") or "")
    if (
        latest_generated
        and latest_generated.date() == target.date()
        and latest_generated >= target - timedelta(minutes=12)
        and latest_phase == slot.phase
    ):
        return RefreshDecision(
            False,
            False,
            schedule,
            f"Watchdog found target covered by snapshot {latest_generated.isoformat()}.",
        )

    return RefreshDecision(
        True,
        True,
        schedule,
        f"Watchdog is recovering the missed {slot.target.strftime('%H:%M')} target.",
    )


def decide_refresh(
    event_name: str,
    schedule: str,
    latest: dict[str, Any] | None,
    now: datetime | None = None,
) -> RefreshDecision:
    if event_name == "workflow_dispatch":
        return RefreshDecision(True, True, "", "Manual run requested.")
    if event_name != "schedule":
        return RefreshDecision(False, True, "", "Push run deploys repository changes.")

    if schedule == WATCHDOG_SCHEDULE:
        return decide_watchdog(latest, now)

    slot = SCHEDULE_SLOTS.get(schedule)
    if slot is None:
        return RefreshDecision(True, True, "", f"Unknown schedule {schedule!r}; run as recovery.")

    local_now = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    target = datetime.combine(local_now.date(), slot.target, tzinfo=CN_TZ)
    earliest = target - timedelta(minutes=12)
    deadline = target + timedelta(minutes=slot.max_delay_minutes)
    latest = latest or {}
    latest_generated = parse_datetime(latest.get("generated_at"))
    latest_phase = str(latest.get("update_phase") or "")

    if local_now < earliest:
        return RefreshDecision(False, False, schedule, "Scheduled event arrived before its valid window.")

    if local_now <= deadline:
        if (
            latest_generated
            and latest_generated >= earliest
            and latest_generated.date() == target.date()
            and latest_phase == slot.phase
        ):
            return RefreshDecision(
                False,
                False,
                schedule,
                f"Target slot already covered by snapshot {latest_generated.isoformat()}.",
            )
        return RefreshDecision(True, True, schedule, "Run the scheduled target slot.")

    if latest_generated:
        snapshot_age = local_now - latest_generated
        if timedelta(0) <= snapshot_age <= timedelta(minutes=45):
            return RefreshDecision(
                False,
                False,
                "",
                f"Late event is covered by recent snapshot {latest_generated.isoformat()}.",
            )

    return RefreshDecision(
        True,
        True,
        "",
        "Scheduled event is late and no recent snapshot exists; run using the actual time phase.",
    )


def load_latest(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def write_github_outputs(path: Path, decision: RefreshDecision) -> None:
    values = {
        "run_generator": str(decision.run_generator).lower(),
        "deploy_required": str(decision.deploy_required).lower(),
        "effective_schedule": decision.effective_schedule,
        "decision": decision.reason,
    }
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deduplicate and recover delayed A-share refresh jobs.")
    parser.add_argument("--event", required=True)
    parser.add_argument("--schedule", default="")
    parser.add_argument("--latest", type=Path, default=Path("data/latest.json"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    decision = decide_refresh(args.event, args.schedule, load_latest(args.latest))
    print(json.dumps(decision.__dict__, ensure_ascii=False))
    if args.github_output:
        write_github_outputs(args.github_output, decision)


if __name__ == "__main__":
    main()
