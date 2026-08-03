"""CLI wrapper for the explicit v1 dataset validator."""

from validator import validate_all


def main() -> None:
    print(f"validated {validate_all()} synthetic cases")


if __name__ == "__main__":
    main()