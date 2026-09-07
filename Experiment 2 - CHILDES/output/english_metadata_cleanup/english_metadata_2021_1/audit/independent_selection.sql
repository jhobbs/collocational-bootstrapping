SELECT u.collection_name, u.corpus_name, u.corpus_id, COUNT(*) AS n_utterances
FROM utterance AS u
WHERE u.collection_name IN ('Eng-NA', 'Eng-UK') AND u.language = 'eng'
AND EXISTS (
  SELECT 1 FROM participant AS p
  WHERE p.id = u.speaker_id AND p.corpus_id = u.corpus_id AND p.collection_id = u.collection_id
    AND p.collection_name IN ('Eng-NA', 'Eng-UK') AND p.language = 'eng'
    AND p.role IN ('Adult', 'Caretaker', 'Father', 'Friend', 'Grandfather', 'Grandmother',
      'Investigator', 'Mother', 'Narrator', 'Playmate', 'Relative', 'Sibling', 'Sister',
      'Brother', 'Teacher', 'Unidentified', 'Visitor', 'Teenager', 'Participant', 'Girl',
      'Male', 'Student', 'Environment', 'Doctor', 'Target_Adult')
)
AND EXISTS (
  SELECT 1 FROM transcript AS t
  WHERE t.id = u.transcript_id AND t.corpus_id = u.corpus_id AND t.collection_id = u.collection_id
    AND t.collection_name IN ('Eng-NA', 'Eng-UK') AND t.language = 'eng'
)
GROUP BY u.collection_name, u.corpus_name, u.corpus_id
