"""S21 (#3 бэклога): dodois_window_cache — immutable baseline-окна /board.

Окна last_week / mtd_lfl заканчиваются в прошлом и округлены до часа →
данные за конкретный window_to_key неизменны. Кэшируем insert-only,
чтобы не дёргать Dodo IS на каждую перегенерацию /board (board-кэш живёт
60с). См. docs CLAUDE.md «#2 DB-cache для LW/MTD_LFL метрик».

Деплой безопасен: пустая таблица = поведение как раньше (всё тянется live
и записывается при первом обращении).

Revision ID: 0026
Revises: 0025
"""
from alembic import op


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pnl_service.dodois_window_cache (
            planfact_key_id  BIGINT NOT NULL
                REFERENCES pnl_service.planfact_keys(id) ON DELETE CASCADE,
            project_id       VARCHAR(64)  NOT NULL,
            metric_type      VARCHAR(32)  NOT NULL,
            window_to_key    VARCHAR(20)  NOT NULL,
            payload          JSONB        NOT NULL,
            computed_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (planfact_key_id, project_id, metric_type, window_to_key)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE pnl_service.dodois_window_cache")
