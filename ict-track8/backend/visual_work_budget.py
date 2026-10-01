"""One process-wide visual work budget, shared by direct and routed calls."""
from contextlib import contextmanager
from threading import BoundedSemaphore

from .config import env_int


VISUAL_WORK_SLOTS = BoundedSemaphore(env_int('ICT8_VISUAL_CONCURRENCY', 4, minimum=1, maximum=16))


class VisualWorkBusy(RuntimeError):
    pass


@contextmanager
def visual_work_slot():
    if not VISUAL_WORK_SLOTS.acquire(blocking=False):
        raise VisualWorkBusy('视觉处理并发已满，请稍后重试')
    try:
        yield
    finally:
        VISUAL_WORK_SLOTS.release()
