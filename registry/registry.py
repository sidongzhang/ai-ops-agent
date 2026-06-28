"""
服务注册表：加载 registry/systems/*.yaml，按 system_id 提供系统/服务查询，
并支持飞书会话 → 系统的路由。文件 mtime 变化时自动热重载（无需重启进程）。
"""
import os
import threading
import logging

import yaml

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
SYSTEMS_DIR = os.path.join(_HERE, 'systems')

_lock = threading.Lock()
_cache: dict = {}          # system_id -> system dict
_chat_index: dict = {}     # chat_id  -> system_id
_fingerprint = None


def project_root() -> str:
    return _ROOT


def _fingerprint_now() -> str:
    if not os.path.isdir(SYSTEMS_DIR):
        return ''
    parts = []
    for fname in sorted(os.listdir(SYSTEMS_DIR)):
        if fname.endswith(('.yaml', '.yml')):
            parts.append(f'{fname}:{os.path.getmtime(os.path.join(SYSTEMS_DIR, fname))}')
    return '|'.join(parts)


def _load(force: bool = False):
    global _fingerprint
    current = _fingerprint_now()
    if not force and current == _fingerprint and _cache:
        return
    with _lock:
        current = _fingerprint_now()
        if not force and current == _fingerprint and _cache:
            return

        cache, chat_index = {}, {}
        if os.path.isdir(SYSTEMS_DIR):
            for fname in sorted(os.listdir(SYSTEMS_DIR)):
                if not fname.endswith(('.yaml', '.yml')):
                    continue
                path = os.path.join(SYSTEMS_DIR, fname)
                try:
                    with open(path, encoding='utf-8') as f:
                        sys_def = yaml.safe_load(f) or {}
                except Exception as e:
                    logger.warning(f'加载系统定义失败 {fname}: {e}')
                    continue
                sid = sys_def.get('id')
                if not sid:
                    logger.warning(f'系统定义缺少 id，跳过: {fname}')
                    continue
                sys_def.setdefault('services', [])
                sys_def.setdefault('infra', {})
                cache[sid] = sys_def
                chat = (sys_def.get('notify') or {}).get('chat_id')
                if chat:
                    chat_index[chat] = sid

        _cache.clear()
        _cache.update(cache)
        _chat_index.clear()
        _chat_index.update(chat_index)
        _fingerprint = current
        logger.info(f'注册表已加载 {len(_cache)} 个系统: {list(_cache)}')


def list_systems() -> list:
    _load()
    return list(_cache.values())


def list_system_ids() -> list:
    _load()
    return list(_cache.keys())


def get_system(system_id: str):
    _load()
    return _cache.get(system_id)


def list_services(system_id: str) -> list:
    system = get_system(system_id)
    return (system or {}).get('services', [])


def get_service(system_id: str, name: str):
    for svc in list_services(system_id):
        if svc.get('name') == name:
            return svc
    return None


def resolve_system_by_chat(chat_id: str):
    """飞书 chat_id → system_id；找不到返回 None。"""
    _load()
    return _chat_index.get(chat_id)


def add_system(sys_def: dict) -> str:
    """把系统描述符落成 YAML 文件并触发热重载，返回 system_id。"""
    sid = sys_def.get('id')
    if not sid:
        raise ValueError('系统定义必须包含 id 字段')
    os.makedirs(SYSTEMS_DIR, exist_ok=True)
    path = os.path.join(SYSTEMS_DIR, f'{sid}.yaml')
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(sys_def, f, allow_unicode=True, sort_keys=False)
    _load(force=True)
    return sid
