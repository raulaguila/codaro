CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    role VARCHAR(32)
);

CREATE VIEW administrators AS
    SELECT id FROM users WHERE role = 'admin';
