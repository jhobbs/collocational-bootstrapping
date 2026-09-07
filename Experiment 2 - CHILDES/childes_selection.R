# Shared metadata rules for the corrected English extraction.
CHILDES_DB_VERSION <- Sys.getenv("CHILDES_DB_VERSION", "2026.1")
ENGLISH_COLLECTIONS <- c("Eng-NA", "Eng-UK")

require_columns <- function(data, columns) {
  missing <- setdiff(columns, names(data))
  if (length(missing)) stop("Missing required metadata: ", paste(missing, collapse = ", "))
}

select_english_participants <- function(participants) {
  require_columns(participants, c("id", "corpus_id", "collection_id", "collection_name", "language", "role"))
  participants %>%
    filter(collection_name %in% ENGLISH_COLLECTIONS, language == "eng",
           !is.na(id), !is.na(corpus_id), !is.na(collection_id), !is.na(role),
           !grepl("target_child", role, ignore.case = TRUE))
}

filter_english_utterances <- function(utterances, speakers, transcripts) {
  require_columns(utterances, c("speaker_id", "transcript_id", "corpus_id", "collection_id", "collection_name", "language"))
  require_columns(transcripts, c("transcript_id", "corpus_id", "collection_id", "collection_name", "language"))
  speaker_keys <- select_english_participants(speakers) %>%
    transmute(speaker_id = as.character(id), corpus_id = as.character(corpus_id),
              collection_id = as.character(collection_id), participant_language = language) %>% distinct()
  transcript_keys <- transcripts %>%
    filter(collection_name %in% ENGLISH_COLLECTIONS, language == "eng") %>%
    transmute(transcript_id = as.character(transcript_id), corpus_id = as.character(corpus_id),
              collection_id = as.character(collection_id), transcript_language = language) %>% distinct()
  utterances %>%
    filter(collection_name %in% ENGLISH_COLLECTIONS, language == "eng") %>%
    mutate(across(c(speaker_id, transcript_id, corpus_id, collection_id), as.character)) %>%
    inner_join(speaker_keys, by = c("speaker_id", "corpus_id", "collection_id"), na_matches = "never", relationship = "many-to-one") %>%
    inner_join(transcript_keys, by = c("transcript_id", "corpus_id", "collection_id"), na_matches = "never", relationship = "many-to-one")
}

get_most_recent_file <- function(directory, pattern) {
  files <- list.files(directory, pattern = pattern, full.names = TRUE)
  if (!length(files)) stop("No files found matching pattern: ", pattern)
  files[which.max(file.info(files)$mtime)]
}

check_speaker_version <- function(speakers) {
  require_columns(speakers, "db_version")
  if (anyNA(speakers$db_version) || any(speakers$db_version != CHILDES_DB_VERSION)) {
    stop("Speaker metadata belongs to a different database release; rerun steps 1 and 2.")
  }
}
