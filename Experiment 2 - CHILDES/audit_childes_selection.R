# Independently count the selected population on the server, without reusing
# the downloader's local joins or selected-ID list.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("Usage: Rscript audit_childes_selection.R RUN_DIRECTORY")
root <- normalizePath(args[[1]])
library(childesr)
if (!nzchar(Sys.getenv("REDIVIS_API_TOKEN"))) {
  Sys.setenv(REDIVIS_API_TOKEN = trimws(paste(readLines(path.expand(Sys.getenv("REDIVIS_TOKEN_FILE", "~/redivis.token")), warn = FALSE), collapse = "")))
}
policy <- read.csv(file.path(root, "audit/selection_policy.csv"), colClasses = "character")
version <- unique(policy$db_version)
if (length(version) != 1L) stop("Ambiguous database version")
sql <- "SELECT u.collection_name, u.corpus_name, u.corpus_id, COUNT(*) AS n_utterances
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
GROUP BY u.collection_name, u.corpus_name, u.corpus_id"
writeLines(sql, file.path(root, "audit/independent_selection.sql"))
counts <- get_sql_query(sql, db_version = version)
if (is.null(counts) || !nrow(counts)) stop("Independent count failed")
write.csv(counts, file.path(root, "audit/independent_selection_counts.csv"), row.names = FALSE)
cat("Independent selected utterance count:", as.character(sum(counts$n_utterances)), "\n")
