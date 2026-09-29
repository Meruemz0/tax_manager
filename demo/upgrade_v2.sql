-- 在已执行 demo/init.sql 的 tax_db 中用 Navicat「运行 SQL 文件」执行。
-- 增量升级；不会重建客户、账号、图片表，也不会覆盖已有报税状态。
-- 可重复执行。执行前建议备份数据库；如有超过 255 字的旧备注，脚本会报错并回滚，
-- 请先人工整理这些备注，再重新运行。请关闭 Navicat 的「出错后继续执行」。

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM customers WHERE length(note) > 255) THEN
        RAISE EXCEPTION '存在超过 255 字的客户备注；请先修改这些备注，再重新运行 upgrade_v2.sql';
    END IF;
END
$$;

ALTER TABLE customers ALTER COLUMN note TYPE VARCHAR(255);

-- 老客户的注册日期用其建档时间在中国时区对应的日期初始化，之后可在页面修改。
ALTER TABLE customers ADD COLUMN IF NOT EXISTS registered_on DATE;
UPDATE customers
SET registered_on = (created_at AT TIME ZONE 'Asia/Shanghai')::date
WHERE registered_on IS NULL;
ALTER TABLE customers
    ALTER COLUMN registered_on SET DEFAULT ((now() AT TIME ZONE 'Asia/Shanghai')::date);
ALTER TABLE customers ALTER COLUMN registered_on SET NOT NULL;

-- 为注册月份至当前中国月份补齐每个月的一行；已有状态保持原值。
INSERT INTO monthly_filings (customer_id, tax_month, is_filed)
SELECT c.id, months.month_start::date, FALSE
FROM customers AS c
CROSS JOIN LATERAL generate_series(
    date_trunc('month', c.registered_on::timestamp),
    date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai'),
    interval '1 month'
) AS months(month_start)
ON CONFLICT (customer_id, tax_month) DO NOTHING;

CREATE TABLE IF NOT EXISTS tag_categories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(60) NOT NULL CHECK (length(btrim(name)) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS tag_categories_name_unique
    ON tag_categories (lower(btrim(name)));

CREATE TABLE IF NOT EXISTS customer_tag_categories (
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    category_id BIGINT NOT NULL REFERENCES tag_categories(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_id, category_id)
);

COMMIT;

-- 检查升级结果：
-- SELECT id, name, registered_on, length(note) AS note_length FROM customers ORDER BY id;
-- SELECT customer_id, tax_month, is_filed FROM monthly_filings ORDER BY customer_id, tax_month;
-- SELECT id, name FROM tag_categories ORDER BY id;
