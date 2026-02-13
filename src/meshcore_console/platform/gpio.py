def gpio_available() -> bool:
    try:
        import RPi.GPIO  # noqa: F401

        return True
    except ImportError:
        return False
