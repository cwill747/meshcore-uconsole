def spi_available() -> bool:
    try:
        import spidev  # noqa: F401

        return True
    except ImportError:
        return False
