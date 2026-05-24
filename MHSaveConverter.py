import sys
import json
import base64
import sqlite3
import os
import random

def safe_b64decode(data):
    if not data:
        return None
    try:
        return base64.b64decode(data)
    except Exception:
        return None

def build_entity_upserts(table_name, entities):
    statements = []
    for entity in entities:
        entity_copy = entity.copy()
        entity_copy["ArchiveData"] = safe_b64decode(entity_copy.get("ArchiveData"))

        columns = ", ".join(entity_copy.keys())
        placeholders = ", ".join("?" for _ in entity_copy)
        update_clause = ", ".join([f"{col}=excluded.{col}" for col in entity_copy if col != "DbGuid"])

        statements.append((
            f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(DbGuid) DO UPDATE SET {update_clause};",
            tuple(entity_copy.values())
        ))
    return statements

def get_table_name(cursor, expected_name):
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)=lower(?) LIMIT 1",
        (expected_name,),
    )
    row = cursor.fetchone()
    return row[0] if row else None

def resolve_email_conflict(cursor, account_table, email):
    while True:
        cursor.execute(f"SELECT Id FROM {account_table} WHERE Email = ?", (email,))
        existing = cursor.fetchone()
        if not existing:
            return ("insert", email, None)

        response = input(
            f"Email '{email}' already exists (Id={existing[0]}). "
            "Choose: [o]verwrite / [r]ename: "
        ).strip().lower()

        if response in ("o", "overwrite"):
            return ("overwrite", email, existing[0])

        if response in ("r", "rename"):
            new_email = input("Enter new email: ").strip()
            if not new_email:
                print("Email cannot be empty.")
                continue
            email = new_email
            continue

        print("Please enter 'o' to overwrite or 'r' to rename.")

def resolve_playername_conflict(cursor, account_table, player_name, ignore_account_id=None):
    while True:
        cursor.execute(f"SELECT Id FROM {account_table} WHERE PlayerName = ?", (player_name,))
        existing = cursor.fetchone()
        if not existing:
            return player_name
        if ignore_account_id is not None and existing[0] == ignore_account_id:
            return player_name

        response = input(
            f"PlayerName '{player_name}' already exists (Id={existing[0]}). "
            "Enter a new name: "
        ).strip()

        if not response:
            print("PlayerName cannot be empty.")
            continue

        player_name = response


def id_exists_in_tables(cursor, account_table, guid_tables, candidate_id):
    cursor.execute(f"SELECT 1 FROM {account_table} WHERE Id=?", (candidate_id,))
    if cursor.fetchone():
        return True

    for table in guid_tables:
        if not table:
            continue
        cursor.execute(f"SELECT 1 FROM {table} WHERE DbGuid=?", (candidate_id,))
        if cursor.fetchone():
            return True
    return False


def generate_unique_global_id(cursor, account_table, guid_tables):
    while True:
        new_id = random.randint(1, 2**63 - 1)
        if not id_exists_in_tables(cursor, account_table, guid_tables, new_id):
            return new_id


def remap_ids_for_renamed_insert(json_data, new_account_id, cursor, account_table, guid_tables):
    id_map = {}

    player = json_data.get("Player", {})
    old_player_id = player.get("DbGuid") if isinstance(player, dict) else None
    if old_player_id is not None:
        id_map[old_player_id] = new_account_id

    for collection_name in ("Avatars", "TeamUps", "Items", "ControlledEntities"):
        for entity in json_data.get(collection_name, []):
            old_id = entity.get("DbGuid")
            if old_id is None:
                continue
            if old_id not in id_map:
                id_map[old_id] = generate_unique_global_id(cursor, account_table, guid_tables)

    if isinstance(player, dict):
        player["DbGuid"] = new_account_id

    for collection_name in ("Avatars", "TeamUps", "Items", "ControlledEntities"):
        for entity in json_data.get(collection_name, []):
            old_id = entity.get("DbGuid")
            if old_id in id_map:
                entity["DbGuid"] = id_map[old_id]

            container_id = entity.get("ContainerDbGuid")
            if container_id in id_map:
                entity["ContainerDbGuid"] = id_map[container_id]


def build_overwrite_cleanup_statements(
    cursor,
    account_table,
    player_table,
    avatar_table,
    teamup_table,
    item_table,
    controlled_entity_table,
    account_id,
    email,
):
    statements = {
        "ControlledEntity": [],
        "Item": [],
        "TeamUp": [],
        "Avatar": [],
        "Player": [],
        "Account": [],
    }

    avatar_ids = []
    teamup_ids = []

    if avatar_table:
        cursor.execute(f"SELECT DbGuid FROM {avatar_table} WHERE ContainerDbGuid=?", (account_id,))
        avatar_ids = [row[0] for row in cursor.fetchall()]

    if teamup_table:
        cursor.execute(f"SELECT DbGuid FROM {teamup_table} WHERE ContainerDbGuid=?", (account_id,))
        teamup_ids = [row[0] for row in cursor.fetchall()]

    if controlled_entity_table and avatar_ids:
        placeholders = ", ".join("?" for _ in avatar_ids)
        statements["ControlledEntity"].append(
            (f"DELETE FROM {controlled_entity_table} WHERE ContainerDbGuid IN ({placeholders})", tuple(avatar_ids))
        )

    if item_table:
        container_ids = [account_id] + avatar_ids + teamup_ids
        placeholders = ", ".join("?" for _ in container_ids)
        statements["Item"].append(
            (f"DELETE FROM {item_table} WHERE ContainerDbGuid IN ({placeholders})", tuple(container_ids))
        )

    if teamup_table:
        statements["TeamUp"].append((f"DELETE FROM {teamup_table} WHERE ContainerDbGuid=?", (account_id,)))

    if avatar_table:
        statements["Avatar"].append((f"DELETE FROM {avatar_table} WHERE ContainerDbGuid=?", (account_id,)))

    if player_table:
        statements["Player"].append((f"DELETE FROM {player_table} WHERE DbGuid=?", (account_id,)))

    statements["Account"].append((f"DELETE FROM {account_table} WHERE Email=?", (email,)))
    return statements

def json_to_sql_inserts(json_data, db_path="Account.db"):
    sql_inserts = {}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    account_table = get_table_name(cursor, "Account")
    player_table = get_table_name(cursor, "Player")
    avatar_table = get_table_name(cursor, "Avatar")
    teamup_table = get_table_name(cursor, "TeamUp")
    item_table = get_table_name(cursor, "Item")
    controlled_entity_table = get_table_name(cursor, "ControlledEntity")
    guid_tables = [player_table, avatar_table, teamup_table, item_table, controlled_entity_table]
    if not account_table or not player_table:
        conn.close()
        raise ValueError(
            f"Database '{db_path}' is missing required tables. "
            f"Found Account={bool(account_table)}, Player={bool(player_table)}."
        )

    user_data = {
        "Id": json_data.get("Id"),
        "Email": json_data.get("Email"),
        "PlayerName": json_data.get("PlayerName"),
        "PasswordHash": safe_b64decode(json_data.get("PasswordHash")),
        "Salt": safe_b64decode(json_data.get("Salt")),
        "UserLevel": json_data.get("UserLevel", 0),
        "Flags": json_data.get("Flags", 0),
    }

    user_data = filter_existing_columns(cursor, account_table, user_data)

    action, resolved_email, existing_account_id = resolve_email_conflict(cursor, account_table, user_data["Email"])
    user_data["Email"] = resolved_email

    user_data["PlayerName"] = resolve_playername_conflict(
        cursor,
        account_table,
        user_data["PlayerName"],
        existing_account_id if action == "overwrite" else None,
    )

    columns = ", ".join(user_data.keys())
    placeholders = ", ".join("?" for _ in user_data)

    query = f"""
    INSERT INTO {account_table} ({columns})
    VALUES ({placeholders})
    """

    if action == "overwrite":
        print(f"Overwriting account '{user_data['Email']}'")

        # Force imported data to reuse the existing account ID
        old_account_id = user_data["Id"]
        user_data["Id"] = existing_account_id
        json_data["Id"] = existing_account_id

        # Remap all entity/container references
        remap_ids_for_renamed_insert(
            json_data,
            existing_account_id,
            cursor,
            account_table,
            guid_tables,
        )

        # Refresh player data after remap
        player = json_data.get("Player", {})
        player["DbGuid"] = existing_account_id

        sql_inserts.update(
            build_overwrite_cleanup_statements(
                cursor,
                account_table,
                player_table,
                avatar_table,
                teamup_table,
                item_table,
                controlled_entity_table,
                existing_account_id,
                user_data["Email"],
            )
        )

        sql_inserts["Account"].append((
            query,
            tuple(user_data.values())
        ))
    else:
        if action == "insert" and resolved_email != json_data.get("Email"):
            print(f"Inserting account with renamed email '{resolved_email}'")
            user_data["Id"] = generate_unique_global_id(cursor, account_table, guid_tables)
            json_data["Id"] = user_data["Id"]
            remap_ids_for_renamed_insert(
                json_data,
                user_data["Id"],
                cursor,
                account_table,
                guid_tables,
            )
        sql_inserts["Account"] = [(
            query,
            tuple(user_data.values())
        )]

    player = json_data.get("Player", {})
    player_copy = player.copy()
    player_copy["ArchiveData"] = safe_b64decode(player_copy.get("ArchiveData"))

    player_copy = filter_existing_columns(cursor, player_table, player_copy)

    columns = ", ".join(player_copy.keys())
    placeholders = ", ".join("?" for _ in player_copy)
    update_clause = ", ".join([f"{col}=excluded.{col}" for col in player_copy if col != "DbGuid"])

    player_upsert_statement = (
        f"INSERT INTO {player_table} ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(DbGuid) DO UPDATE SET {update_clause};",
        tuple(player_copy.values())
    )
    if action == "overwrite":
        sql_inserts["Player"].append(player_upsert_statement)
    else:
        sql_inserts["Player"] = [player_upsert_statement]

    if avatar_table:
        avatar_upserts = build_entity_upserts(avatar_table, json_data.get("Avatars", []))
        if action == "overwrite":
            sql_inserts["Avatar"].extend(avatar_upserts)
        else:
            sql_inserts["Avatar"] = avatar_upserts
    else:
        print("Warning: Avatar table not found; skipping Avatars import.")

    if teamup_table:
        teamup_upserts = build_entity_upserts(teamup_table, json_data.get("TeamUps", []))
        if action == "overwrite":
            sql_inserts["TeamUp"].extend(teamup_upserts)
        else:
            sql_inserts["TeamUp"] = teamup_upserts
    else:
        print("Warning: TeamUp table not found; skipping TeamUps import.")

    if item_table:
        item_upserts = build_entity_upserts(item_table, json_data.get("Items", []))
        if action == "overwrite":
            sql_inserts["Item"].extend(item_upserts)
        else:
            sql_inserts["Item"] = item_upserts
    else:
        print("Warning: Item table not found; skipping Items import.")

    if controlled_entity_table:
        controlled_entity_upserts = build_entity_upserts(controlled_entity_table, json_data.get("ControlledEntities", []))
        if action == "overwrite":
            sql_inserts["ControlledEntity"].extend(controlled_entity_upserts)
        else:
            sql_inserts["ControlledEntity"] = controlled_entity_upserts
    else:
        print("Warning: ControlledEntity table not found; skipping ControlledEntities import.")

    conn.close()
    return sql_inserts

def filter_existing_columns(cursor, table_name, data_dict):
    cursor.execute(f"PRAGMA table_info({table_name})")
    valid_columns = {row[1] for row in cursor.fetchall()}

    return {
        key: value
        for key, value in data_dict.items()
        if key in valid_columns
    }

def load_json_from_file(file_path):
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
        input("Press any key to exit...")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format. {e}")
        input("Press any key to exit...")
    return None

def insert_into_database(sql_statements, db_path="Account.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for table, statements in sql_statements.items():
        for query, values in statements:
            cursor.execute(query, values)

    conn.commit()
    conn.close()
    print(f"Data inserted into {db_path}")

json_file_path = sys.argv[1] if len(sys.argv) > 1 else "tahiti.json"
db_file_path = sys.argv[2] if len(sys.argv) > 2 else "Account.db"

json_data = load_json_from_file(json_file_path)

if json_data:
    if not os.path.exists(db_file_path):
        print(f"Error: Database file not found at {db_file_path}")
        input("Press any key to exit...")
        sys.exit(1)

    try:
        sql_statements = json_to_sql_inserts(json_data, db_file_path)
        insert_into_database(sql_statements, db_file_path)
        input("Press any key to exit...")
        sys.exit(1)

    except ValueError as e:
        print(f"Error: {e}")
        input("Press any key to exit...")
        sys.exit(1)

    except Exception as e:
        print(f"Unexpected error: {e}")
        input("Press any key to exit...")
        sys.exit(1)
