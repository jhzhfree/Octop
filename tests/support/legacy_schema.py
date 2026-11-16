"""Construct historical layouts only in disposable synthetic migration tests."""


def remove_v49_guards_for_fixture(connection):
    # A pre-49 fixture must not carry constraints that did not exist then.
    # Never expose this helper through application/maintenance code.
    for name in (
        "mattermost_retired_broker_no_enable",
        "octop_schema_floor_update",
        "octop_schema_floor_insert",
        "octop_schema_floor_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")
