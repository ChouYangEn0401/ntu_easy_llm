from functools import wraps

# =========================
# Text Encapsulation Utils
# =========================

def encap_text(title: str, content: str, seperator: str = '```') -> str:
    return f"{title}\n{seperator}\n{content}\n{seperator}\n"

def encap_text_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return encap_text("", func(*args, **kwargs))
    return wrapper


def encap_text_with_title_decorator(title: str, separator: str = "```"):
    def _decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return encap_text(title, func(*args, **kwargs), separator)
        return wrapper
    return _decorator


