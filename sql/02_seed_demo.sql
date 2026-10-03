-- Optional: seed a small, generic stewardship model so the Domain Owners tab
-- and role resolution have data in a fresh demo. Replace names/emails with real
-- values (or edit them live on the Domain Owners tab as a governance lead).
--   {{WF_CATALOG}}  e.g. main
--   {{WF_SCHEMA}}   e.g. data_governance_app

INSERT INTO {{WF_CATALOG}}.{{WF_SCHEMA}}.data_stewards
  (data_domain, data_steward_name, data_steward_email, data_owner_name, data_owner_email)
VALUES
  ('Finance',       'Steward One',   'steward.one@example.com',   'Owner One',   'owner.one@example.com'),
  ('Operations',    'Steward Two',   'steward.two@example.com',   'Owner Two',   'owner.two@example.com'),
  ('Commercial',    'Steward Three', 'steward.three@example.com', 'Owner Three', 'owner.three@example.com'),
  ('Engineering',   'Steward Four',  'steward.four@example.com',  'Owner Four',  'owner.four@example.com'),
  ('External Data', 'Steward Five',  'steward.five@example.com',  'Owner Five',  'owner.five@example.com');
