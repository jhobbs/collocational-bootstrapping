# Retrieve utterances while preserving the selected speaker and transcript scope.
library(childesr)
library(dplyr)
source(file.path(Sys.getenv("CHILDES_CODE_DIR", "."), "childes_selection.R"))
timestamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
input_file <- get_most_recent_file("rdata/speakers", "^childes_filtered_speakers_.*\\.csv$")
speakers <- read.csv(input_file, colClasses = c(id = "character", corpus_id = "character", collection_id = "character", db_version = "character"))
check_speaker_version(speakers)
speakers <- select_english_participants(speakers)
if (!nrow(speakers)) stop("No eligible speakers remain")
dir.create("audit", showWarnings = FALSE, recursive = TRUE)
write.csv(speakers, "audit/selected_speakers.csv", row.names = FALSE)

transcripts <- get_transcripts(collection = ENGLISH_COLLECTIONS, db_version = CHILDES_DB_VERSION)
if (is.null(transcripts) || !nrow(transcripts)) stop("Transcript retrieval returned no data")
write.csv(transcripts, "audit/transcripts.csv", row.names = FALSE)
requested <- speakers %>% distinct(collection_name, collection_id, corpus_name, corpus_id)
utterance_list <- list()
retrieval_counts <- list()
for (i in seq_len(nrow(requested))) {
  request <- requested[i, ]
  selected <- speakers %>% filter(corpus_id == request$corpus_id, collection_id == request$collection_id)
  cat(sprintf("[%d/%d] %s / %s: %d selected speakers\n", i, nrow(requested),
              request$collection_name, request$corpus_name, nrow(selected)))
  # Fetch each collection/corpus once, then enforce exact identities locally.
  # Errors and NULL responses stop the build instead of publishing a partial CSV.
  utterances <- get_utterances(collection = request$collection_name, corpus = request$corpus_name,
                              language = "eng", role = unique(selected$role), db_version = CHILDES_DB_VERSION)
  if (is.null(utterances)) stop("Utterance retrieval failed for ", request$corpus_name)
  retained <- filter_english_utterances(utterances, selected, transcripts)
  retrieval_counts[[i]] <- mutate(request, n_retrieved = nrow(utterances), n_retained = nrow(retained))
  write.csv(bind_rows(retrieval_counts), "audit/retrieval_counts.csv", row.names = FALSE)
  cat("  Retrieved:", nrow(utterances), "Retained:", nrow(retained), "\n")
  if (nrow(retained)) utterance_list[[length(utterance_list) + 1L]] <- retained
}
all_utterances <- bind_rows(utterance_list)
if (!nrow(all_utterances)) stop("No eligible utterances were retrieved")

# get_utterances already returns one full utterance per row. Verify its key
# instead of silently joining distinct utterances or spending time regrouping.
keys <- select(all_utterances, transcript_id, speaker_id, utterance_order)
if (anyDuplicated(keys)) stop("Duplicate utterance keys; refusing to merge distinct source rows")
full_utterances <- all_utterances %>%
  transmute(transcript_id, speaker_id, utterance_order, full_utterance = gloss,
            speaker_code, speaker_name, speaker_role, target_child_name, target_child_age,
            target_child_sex, corpus_name, utterance_type = type, num_tokens,
            collection_name, collection_id, corpus_id, language, participant_language,
            transcript_language, db_version = CHILDES_DB_VERSION, utterance_id = id) %>%
  arrange(transcript_id, utterance_order)
write.csv(count(full_utterances, collection_name, corpus_name, language, participant_language,
                transcript_language, name = "n_utterances"), "audit/export_counts.csv", row.names = FALSE)
dir.create("rdata/utterances", showWarnings = FALSE, recursive = TRUE)
outfile <- paste0("rdata/utterances/childes_full_utterances_", timestamp, ".csv")
write.csv(full_utterances, outfile, row.names = FALSE)
dir.create("data", showWarnings = FALSE, recursive = TRUE)
if (!file.copy(outfile, "data/childes_utterances.csv", overwrite = TRUE)) stop("CSV copy failed")
cat("BUILD COMPLETE:", nrow(full_utterances), "utterances saved to data/childes_utterances.csv\n")
