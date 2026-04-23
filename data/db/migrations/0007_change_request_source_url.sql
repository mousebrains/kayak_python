-- Migration 0007: add change_request.source_url.
--
-- Captures the page the submitter was on when they clicked
-- Comment/Propose/Contact, so the maintainer reviewing the queue can
-- see what triggered the submission without having to infer it from the
-- target_type/target_id alone. Contact-form submissions do not write to
-- change_request (they email directly), but they also thread source_url
-- through the email body. Nullable because old rows don't have it and
-- because the referrer can legitimately be missing.
ALTER TABLE change_request ADD COLUMN source_url TEXT;
