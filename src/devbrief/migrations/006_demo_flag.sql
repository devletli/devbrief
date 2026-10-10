-- 006_demo_flag: demo bolumler hicbir modda public olamaz.
ALTER TABLE episodes ADD COLUMN is_demo INT DEFAULT 0;
