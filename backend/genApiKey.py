"""Deprecated helper kept only to point operators to the safe credentials flow."""


def main() -> None:
    raise SystemExit(
        "Server-side private-key CLOB credential generation is disabled. "
        "Use the frontend Polymarket wallet profile page to generate a manual import command instead."
    )


if __name__ == "__main__":
    main()
