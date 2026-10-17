"""Structured observations captured at the connector boundary, never model text."""
import re
from datetime import datetime, timezone


class HealthText(str):
    def __new__(cls, text, observation):
        value = super().__new__(cls, text)
        value.health_observation = observation
        return value


def health_result(text, entries, descriptor, *, requested_service=None):
    names = [s.get('name') for s in descriptor.get('services', []) if s.get('name')]
    observed = []
    for entry in entries:
        detail = str(entry.get('detail', ''))
        http = re.search(r'\bHTTP\s+([1-5][0-9]{2})(?!\d)', detail)
        observed.append({'service': str(entry.get('name', '')), 'ok': entry.get('ok') if type(entry.get('ok')) is bool else None,
                         'http_status': int(http[1]) if http else None})
    return HealthText(text, {'version': 1, 'scope': 'single_service' if requested_service else 'registered_services',
        'requested_service': requested_service, 'registered_services': names, 'services': observed,
        'complete': requested_service is None and len(names) == len(set(names)) and
                    len(observed) == len(names) and {s['service'] for s in observed} == set(names) and
                    all(s['ok'] is not None for s in observed),
        'observed_at': datetime.now(timezone.utc).isoformat()})
