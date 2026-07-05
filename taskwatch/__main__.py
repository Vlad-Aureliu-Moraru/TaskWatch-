import sys

from .cli import run
from .log import setup_logger


def main():
    setup_logger()
    run(sys.argv[1:])


if __name__ == "__main__":
    main()
