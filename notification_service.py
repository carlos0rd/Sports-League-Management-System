"""Notification detection and persistence for followed teams."""

from __future__ import annotations

from typing import Any

NOTIFICATION_TYPE_UPCOMING = "upcoming_match"
NOTIFICATION_TYPE_NEW_MATCH = "new_match"
NOTIFICATION_TYPE_RESCHEDULED = "match_rescheduled"
NOTIFICATION_TYPE_SCORE_CHANGE = "score_change"
NOTIFICATION_TYPE_FINAL_RESULT = "final_result"
NOTIFICATION_TYPE_STATUS_CHANGE = "match_status_change"
NOTIFICATION_TYPE_STANDINGS_UPDATE = "standings_update"

LIVE_STATUSES = ("IN_PLAY", "PAUSED", "LIVE")
UPCOMING_STATUSES = ("SCHEDULED", "TIMED", "POSTPONED")


def _format_score(home: int | None, away: int | None) -> str:
    home_val = home if home is not None else "-"
    away_val = away if away is not None else "-"
    return f"{home_val}-{away_val}"


def _format_date(value) -> str:
    if not value:
        return "soon"
    try:
        return value.strftime("%b %d, %Y %H:%M")
    except Exception:
        return str(value)


def _get_team_names(cur, home_team_id: int, away_team_id: int) -> tuple[str, str]:
    cur.execute(
        """
        SELECT
            (SELECT name FROM teams WHERE team_id = %s),
            (SELECT name FROM teams WHERE team_id = %s)
        """,
        (home_team_id, away_team_id),
    )
    row = cur.fetchone()
    if not row:
        return ("Home", "Away")

    return row[0] or "Home", row[1] or "Away"


def get_match_followers(
    cur,
    home_team_id: int,
    away_team_id: int,
    league_id: int | None = None,
) -> list[int]:
    """
    Return users who follow one of the two teams.

    Important:
    This does NOT notify league followers.
    Only users following home_team_id or away_team_id receive match notifications.
    """
    cur.execute(
        """
        SELECT DISTINCT uf.user_id
        FROM user_favorites uf
        WHERE uf.entity_type = 'team'
          AND uf.entity_id IN (%s, %s)
        """,
        (home_team_id, away_team_id),
    )
    return [row[0] for row in cur.fetchall()]


def get_team_followers(cur, team_id: int) -> list[int]:
    cur.execute(
        """
        SELECT DISTINCT user_id
        FROM user_favorites
        WHERE entity_type = 'team'
          AND entity_id = %s
        """,
        (team_id,),
    )
    return [row[0] for row in cur.fetchall()]


def create_notification(
    cur,
    user_id: int,
    notification_type: str,
    message: str,
    match_id: int | None = None,
    *,
    update_on_conflict: bool = False,
) -> bool:
    """
    Insert notification.

    If match_id is provided, duplicate notifications are prevented by:
    (user_id, type, related_match_id)

    For non-match notifications, match_id can be None.
    """
    if update_on_conflict and match_id is not None:
        cur.execute(
            """
            INSERT INTO notifications (user_id, type, message, related_match_id, is_read)
            VALUES (%s, %s, %s, %s, FALSE)
            ON CONFLICT (user_id, type, related_match_id)
            DO UPDATE SET
                message = EXCLUDED.message,
                is_read = FALSE,
                created_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (user_id, notification_type, message, match_id),
        )
    elif match_id is not None:
        cur.execute(
            """
            INSERT INTO notifications (user_id, type, message, related_match_id, is_read)
            VALUES (%s, %s, %s, %s, FALSE)
            ON CONFLICT (user_id, type, related_match_id)
            DO NOTHING
            RETURNING id
            """,
            (user_id, notification_type, message, match_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO notifications (user_id, type, message, related_match_id, is_read)
            VALUES (%s, %s, %s, NULL, FALSE)
            RETURNING id
            """,
            (user_id, notification_type, message),
        )

    return cur.fetchone() is not None


def detect_upcoming_matches(cur, hours_ahead: int = 24) -> dict[str, int]:
    """
    Upcoming match:
    Notify only if:
    1. User follows home team or away team.
    2. Match is scheduled.
    3. Match is between NOW and NOW + 24 hours.
    """
    counts = {"upcoming_match": 0}

    cur.execute(
        """
        SELECT
            m.match_id,
            m.home_team_id,
            m.away_team_id,
            m.league_id,
            m.utc_date,
            ht.name AS home_name,
            at.name AS away_name
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.team_id
        JOIN teams at ON m.away_team_id = at.team_id
        WHERE m.status IN ('SCHEDULED', 'TIMED', 'POSTPONED')
          AND m.utc_date >= NOW()
          AND m.utc_date <= NOW() + (%s || ' hours')::INTERVAL
        ORDER BY m.utc_date ASC
        """,
        (hours_ahead,),
    )

    for row in cur.fetchall():
        match_id, home_team_id, away_team_id, league_id, utc_date, home_name, away_name = row

        followers = get_match_followers(cur, home_team_id, away_team_id, league_id)
        if not followers:
            continue

        message = f"Upcoming match: {home_name} vs {away_name} on {_format_date(utc_date)}"

        for user_id in followers:
            if create_notification(
                cur,
                user_id,
                NOTIFICATION_TYPE_UPCOMING,
                message,
                match_id,
            ):
                counts["upcoming_match"] += 1

    return counts


def notify_new_match(
    cur,
    match_id: int,
    home_team_id: int,
    away_team_id: int,
    league_id: int | None,
    utc_date,
) -> dict[str, int]:
    """
    New match scheduled:
    Notify users who follow one of the two teams when admin creates a match.
    """
    counts = {"new_match": 0}

    followers = get_match_followers(cur, home_team_id, away_team_id, league_id)
    if not followers:
        return counts

    home_name, away_name = _get_team_names(cur, home_team_id, away_team_id)

    message = f"New match scheduled: {home_name} vs {away_name} on {_format_date(utc_date)}"

    for user_id in followers:
        if create_notification(
            cur,
            user_id,
            NOTIFICATION_TYPE_NEW_MATCH,
            message,
            match_id,
        ):
            counts["new_match"] += 1

    return counts


def notify_match_rescheduled(
    cur,
    match_id: int,
    home_team_id: int,
    away_team_id: int,
    league_id: int | None,
    old_date,
    new_date,
) -> dict[str, int]:
    """
    Match rescheduled:
    Notify when admin changes the match date.
    """
    counts = {"match_rescheduled": 0}

    if old_date == new_date:
        return counts

    followers = get_match_followers(cur, home_team_id, away_team_id, league_id)
    if not followers:
        return counts

    home_name, away_name = _get_team_names(cur, home_team_id, away_team_id)

    message = (
        f"Match rescheduled: {home_name} vs {away_name} "
        f"from {_format_date(old_date)} to {_format_date(new_date)}"
    )

    for user_id in followers:
        if create_notification(
            cur,
            user_id,
            NOTIFICATION_TYPE_RESCHEDULED,
            message,
            match_id,
            update_on_conflict=True,
        ):
            counts["match_rescheduled"] += 1

    return counts


def notify_match_status_change(
    cur,
    match_id: int,
    home_team_id: int,
    away_team_id: int,
    league_id: int | None,
    old_status: str | None,
    new_status: str | None,
) -> dict[str, int]:
    """
    Match postponed/cancelled:
    Notify only when status changes to POSTPONED or CANCELLED.
    """
    counts = {"match_status_change": 0}

    if not new_status:
        return counts

    if old_status == new_status:
        return counts

    if new_status not in ("POSTPONED", "CANCELLED"):
        return counts

    followers = get_match_followers(cur, home_team_id, away_team_id, league_id)
    if not followers:
        return counts

    home_name, away_name = _get_team_names(cur, home_team_id, away_team_id)

    if new_status == "POSTPONED":
        message = f"Match postponed: {home_name} vs {away_name}"
    else:
        message = f"Match cancelled: {home_name} vs {away_name}"

    for user_id in followers:
        if create_notification(
            cur,
            user_id,
            NOTIFICATION_TYPE_STATUS_CHANGE,
            message,
            match_id,
            update_on_conflict=True,
        ):
            counts["match_status_change"] += 1

    return counts


def process_match_notification_events(
    cur,
    match_id: int,
    home_team_id: int,
    away_team_id: int,
    league_id: int | None,
    old_status: str | None,
    new_status: str,
    old_home: int | None,
    old_away: int | None,
    new_home: int | None,
    new_away: int | None,
) -> dict[str, int]:
    """
    Score update and final result.
    """
    counts = {
        "score_change": 0,
        "final_result": 0,
        "match_status_change": 0,
    }

    followers = get_match_followers(cur, home_team_id, away_team_id, league_id)
    if not followers:
        return counts

    home_name, away_name = _get_team_names(cur, home_team_id, away_team_id)

    score_changed = (new_home, new_away) != (old_home, old_away)
    has_score = new_home is not None and new_away is not None

    if (
        new_status in LIVE_STATUSES
        and score_changed
        and has_score
        and (old_home is not None or old_away is not None or (new_home, new_away) != (0, 0))
    ):
        message = f"Score update: {home_name} {_format_score(new_home, new_away)} {away_name} (live)"

        for user_id in followers:
            if create_notification(
                cur,
                user_id,
                NOTIFICATION_TYPE_SCORE_CHANGE,
                message,
                match_id,
                update_on_conflict=True,
            ):
                counts["score_change"] += 1

    if new_status == "FINISHED" and old_status != "FINISHED" and has_score:
        message = f"Final result: {home_name} {_format_score(new_home, new_away)} {away_name}"

        for user_id in followers:
            if create_notification(
                cur,
                user_id,
                NOTIFICATION_TYPE_FINAL_RESULT,
                message,
                match_id,
            ):
                counts["final_result"] += 1

    status_counts = notify_match_status_change(
        cur,
        match_id,
        home_team_id,
        away_team_id,
        league_id,
        old_status,
        new_status,
    )

    counts["match_status_change"] += status_counts["match_status_change"]

    return counts


def notify_standings_change(
    cur,
    team_id: int,
    old_position: int | None,
    new_position: int | None,
) -> dict[str, int]:
    """
    Standings update:
    Notify users who follow the team if its position changes.
    """
    counts = {"standings_update": 0}

    if old_position is None or new_position is None:
        return counts

    if old_position == new_position:
        return counts

    followers = get_team_followers(cur, team_id)
    if not followers:
        return counts

    cur.execute("SELECT name FROM teams WHERE team_id = %s", (team_id,))
    team_row = cur.fetchone()
    team_name = team_row[0] if team_row else "Your team"

    if new_position < old_position:
        movement = f"moved up from position {old_position} to {new_position}"
    else:
        movement = f"dropped from position {old_position} to {new_position}"

    message = f"Standings update: {team_name} {movement}"

    for user_id in followers:
        if create_notification(
            cur,
            user_id,
            NOTIFICATION_TYPE_STANDINGS_UPDATE,
            message,
            None,
        ):
            counts["standings_update"] += 1

    return counts


def run_notification_detection(db) -> dict[str, Any]:
    """
    Passive detection:
    Runs when user opens dashboard.
    """
    cur = db.cursor()

    try:
        counts = detect_upcoming_matches(cur, hours_ahead=24)
        db.commit()
        return counts

    except Exception:
        db.rollback()
        raise

    finally:
        cur.close()


def notification_row_to_dict(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "type": row[1],
        "message": row[2],
        "related_match_id": row[3],
        "is_read": row[4],
        "created_at": row[5].isoformat() if row[5] else None,
    }