import json
import os
from datetime import datetime
from datetime import timezone
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.mgmt.monitor import MonitorManagementClient
from .storage import upload_bytes


def _credential():
    tenant = os.getenv('AZURE_TENANT_ID')
    client_id = os.getenv('AZURE_CLIENT_ID')
    client_secret = os.getenv('AZURE_CLIENT_SECRET')
    if tenant and client_id and client_secret:
        return ClientSecretCredential(tenant_id=tenant, client_id=client_id, client_secret=client_secret)
    return DefaultAzureCredential()


def _event_dict(event):
    resource_id = getattr(event, 'resource_id', None)
    resource_group = None
    resource_type = None
    if resource_id:
        parts = resource_id.split('/')
        lower = [p.lower() for p in parts]
        try:
            i = lower.index('resourcegroups')
            resource_group = parts[i + 1]
        except (ValueError, IndexError):
            pass
        try:
            i = lower.index('providers')
            resource_type = '/'.join(parts[i + 1:])
        except (ValueError, IndexError):
            pass
    status = getattr(event, 'status', None)
    if status:
        status = getattr(status, 'value', None) or str(status)
    operation = getattr(event, 'operation_name', None)
    operation = getattr(operation, 'value', None) or str(operation or '')
    return {
        'event_time': event.event_timestamp.isoformat() if getattr(event, 'event_timestamp', None) else None,
        'operation_name': operation,
        'resource_id': resource_id,
        'resource_group': resource_group,
        'resource_type': resource_type,
        'status': status,
        'caller': getattr(event, 'caller', None),
        'correlation_id': getattr(event, 'correlation_id', None),
        'raw': event.as_dict() if hasattr(event, 'as_dict') else {},
    }


def fetch_activity_events(start_time, end_time):
    subscription_id = os.getenv('AZURE_SUBSCRIPTION_ID')
    if not subscription_id:
        raise RuntimeError('AZURE_SUBSCRIPTION_ID is not configured')
    credential = _credential()
    client = MonitorManagementClient(credential, subscription_id)
    start_iso = start_time.astimezone(timezone.utc).isoformat()
    end_iso = end_time.astimezone(timezone.utc).isoformat()
    filter_value = f"eventTimestamp ge '{start_iso}' and eventTimestamp le '{end_iso}'"
    return [_event_dict(event) for event in client.activity_logs.list(filter=filter_value)]


def upload_activity_batch(user_id, practice_id, events):
    """Persist the exact fetched Activity Log batch to configured cloud storage."""
    if not events:
        return None
    stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    name = f'users/{user_id}/practices/{practice_id}/azure-activity/activity-{stamp}.json'
    payload = json.dumps({'practice_id': practice_id, 'events': events}, ensure_ascii=False, default=str, indent=2).encode('utf-8')
    upload_bytes(name, payload, 'application/json')
    return name
