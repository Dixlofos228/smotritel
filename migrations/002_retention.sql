CREATE INDEX idx_outbox_pending ON outbox(state,next_attempt);
CREATE INDEX idx_inbox_state ON inbox(state,update_id);
