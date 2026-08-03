def pytest_addoption(parser):
    parser.addoption(
        "--run-real-api",
        action="store_true",
        default=False,
        help="allow explicitly opted-in paid external API tests",
    )
