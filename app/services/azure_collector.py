import os
import time
from datetime import datetime, timedelta, timezone
from flask import Flask
from .. import db
from ..models.practice import PracticeSession, AzureActivity
from .azure_activity import fetch_activity_events, upload_activity_batch


def _naive(dt):
    if dt is None:
        return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def collect_for_practice(practice):
    now = datetime.utcnow()
    start = practice.started_at
    end = now if (practice.status or '').lower() in {'in progress', 'in_progress', 'active'} else practice.ended_at
    if not start or not end or end < start:
        return 0
    events = fetch_activity_events(start.replace(tzinfo=timezone.utc), end.replace(tzinfo=timezone.utc))
    new_events = []
    existing_keys = {
        (a.event_time, a.operation_name, a.correlation_id, a.resource_id)
        for a in AzureActivity.query.filter_by(practice_id=practice.id).all()
    }
    for e in events:
        event_time = e.get('event_time')
        parsed = datetime.fromisoformat(event_time.replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None) if event_time else None
        key = (parsed, e.get('operation_name'), e.get('correlation_id'), e.get('resource_id'))
        if key in existing_keys:
            continue
        row = AzureActivity(
            practice_id=practice.id,
            event_time=parsed,
            operation_name=e.get('operation_name'),
            resource_id=e.get('resource_id'),
            resource_group=e.get('resource_group'),
            resource_type=e.get('resource_type'),
            status=e.get('status'),
            caller=e.get('caller'),
            correlation_id=e.get('correlation_id'),
            raw_event_json=e.get('raw') or e,
        )
        db.session.add(row)
        new_events.append(e)
        existing_keys.add(key)
    if new_events:
        # Archive the exact raw batch before committing the database rows. If storage
        # fails, the transaction is rolled back and the next poll can retry it.
        upload_activity_batch(practice.user_id, practice.id, new_events)
    db.session.commit()
    return len(new_events)


def run_loop(app):
    interval = max(int(os.getenv('AZURE_ACTIVITY_POLL_SECONDS', '300')), 60)
    with app.app_context():
        while True:
            try:
                cutoff = datetime.utcnow() - timedelta(hours=24)
                practices = PracticeSession.query.filter(PracticeSession.provider == 'Azure').filter(
                    (PracticeSession.status.in_(['In Progress', 'Draft', 'Active', 'in_progress'])) |
                    (PracticeSession.ended_at >= cutoff)
                ).all()
                for practice in practices:
                    try:
                        collect_for_practice(practice)
                    except Exception as exc:
                        app.logger.exception('Azure Activity collector failed for practice %s: %s', practice.id, exc)
            except Exception as exc:
                app.logger.exception('Azure Activity collector loop failed: %s', exc)
            time.sleep(interval)
