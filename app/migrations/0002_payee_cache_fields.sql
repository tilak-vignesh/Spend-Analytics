-- Payee cache also remembers the parsed name and whether it's an individual's name
-- (needed for the P2P default on merchant QRs that carry a person's name).
ALTER TABLE merchant_category_map ADD COLUMN payee_name TEXT;
ALTER TABLE merchant_category_map ADD COLUMN is_person_name INTEGER CHECK (is_person_name IN (0, 1));
