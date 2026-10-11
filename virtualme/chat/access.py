import logging
from plugins.config import WHITE_LIST_FILE
from plugins.group_access import read_active_groups, update_active_groups

logger = logging.getLogger(__name__)

def load_white_list() -> set:
    try:
        return read_active_groups(WHITE_LIST_FILE)
    except (OSError, ValueError, TypeError):
        logger.exception("load_white_list failed; preserving last valid groups")
        return set(active_groups)


def save_white_list(data) -> None:
    # Add only the requested groups to the latest disk state, never a stale cache.
    groups = update_active_groups(WHITE_LIST_FILE, add=data)
    active_groups.clear()
    active_groups.update(groups)


active_groups = set()
active_groups.update(load_white_list())


def is_group_active(group_id: int) -> bool:
    return group_id in get_active_group_ids()


def get_active_group_ids() -> set[int]:
    groups = load_white_list()
    active_groups.clear()
    active_groups.update(groups)
    return set(active_groups)
