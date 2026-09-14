ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user';

UPDATE users
SET role = 'super_admin'
WHERE LOWER(TRIM(email)) = 'yuldashevrozalibek1@gmail.com';
