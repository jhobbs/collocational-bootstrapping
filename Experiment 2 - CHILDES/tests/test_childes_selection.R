# Run from Experiment 2 - CHILDES: Rscript tests/test_childes_selection.R
library(dplyr)
if (!file.exists("childes_selection.R")) stop("English metadata selection is not implemented")
source("childes_selection.R")

# Catch the historical entry point: an English-labelled German investigator.
participants <- tibble(
  id = 1:6, corpus_id = c(10, 20, 10, 10, 30, 40),
  collection_id = c(1, 2, 1, 1, 3, 4),
  collection_name = c("Eng-NA", "German", "Eng-NA", "Eng-NA", "Eng-UK", "Biling"),
  corpus_name = c("Example", "Rigol", "Example", "Example", "ExampleUK", "Leo"),
  language = c("eng", "eng", "eng deu", NA, "eng", "eng"),
  role = c("Mother", "Investigator", "Father", "Mother", "Target_Child", "Mother")
)
selected <- select_english_participants(participants)
stopifnot(identical(selected$id, 1L))

transcripts <- tibble(
  transcript_id = c(100, 101, 102, 103), corpus_id = c(10, 20, 10, 10),
  collection_id = c(1, 2, 1, 1),
  collection_name = c("Eng-NA", "German", "Eng-NA", "Eng-NA"),
  language = c("eng", "eng", "eng deu", NA)
)
utterances <- tibble(
  id = 1:9, transcript_id = c(100, 101, 100, 102, 100, 999, 100, 103, 100),
  speaker_id = c(1, 2, 99, 1, 1, 1, 1, 1, 1),
  corpus_id = c(10, 20, 10, 10, 10, 10, 99, 10, 10),
  collection_id = c(1, 2, 1, 1, 1, 1, 1, 1, 1),
  collection_name = c("Eng-NA", "German", rep("Eng-NA", 7)),
  language = c("eng", "eng", "eng", "eng", "deu", "eng", "eng", "eng", NA),
  gloss = c("I see it", "ich sehe es", "wrong speaker", "bilingual transcript",
            "German label", "unknown transcript", "wrong corpus ID", "unknown language", "missing label")
)
# Each removed row isolates one actual contamination mechanism. A duplicate
# participant metadata row must not duplicate the accepted utterance.
kept <- filter_english_utterances(utterances, bind_rows(selected, selected), transcripts)
stopifnot(identical(kept$id, 1L), identical(kept$gloss, "I see it"))
stopifnot(identical(kept$participant_language, "eng"),
          identical(kept$transcript_language, "eng"))
empty <- filter_english_utterances(utterances[0, ], selected, transcripts)
stopifnot(nrow(empty) == 0L)
bad <- try(filter_english_utterances(select(utterances, -speaker_id), selected, transcripts), silent = TRUE)
stopifnot(inherits(bad, "try-error"))
# Redivis returns 64-bit IDs, while CSV-loaded metadata can be character IDs.
# They must join without coercion errors or precision loss.
large_speakers <- selected
large_speakers$id <- "132000000016"
large_utterances <- utterances[1, ]
large_utterances$speaker_id <- bit64::as.integer64("132000000016")
large_kept <- filter_english_utterances(large_utterances, large_speakers, transcripts)
stopifnot(nrow(large_kept) == 1L, as.character(large_kept$speaker_id) == "132000000016")
cat("PASS: collection, participant language, transcript language, utterance language, speaker identity, missing metadata, and duplicate metadata\n")

# Exercise the real export script with only the remote API replaced. Removing
# the identity join would admit row 3 even though its speaker has the same role.
local({
  source_dir <- normalizePath(".")
  previous <- getwd()
  run_dir <- tempfile("childes-selection-test-")
  dir.create(file.path(run_dir, "rdata/speakers"), recursive = TRUE)
  on.exit({setwd(previous); unlink(run_dir, recursive = TRUE); Sys.unsetenv("CHILDES_CODE_DIR")})
  selected$db_version <- CHILDES_DB_VERSION
  write.csv(selected, file.path(run_dir, "rdata/speakers/childes_filtered_speakers_test.csv"), row.names = FALSE)
  returned <- utterances[c(1, 3), ]
  returned$utterance_order <- 1:2
  returned$speaker_code <- "MOT"
  returned$speaker_name <- "Mother"
  returned$speaker_role <- "Mother"
  returned$target_child_name <- "Child"
  returned$target_child_age <- 30
  returned$target_child_sex <- "female"
  returned$corpus_name <- "Example"
  returned$type <- "declarative"
  returned$num_tokens <- 3
  returned <- bind_rows(returned, mutate(returned[1, ], id = 10L, transcript_id = 9,
                                        gloss = "you go", utterance_order = 1L))
  environment <- new.env(parent = globalenv())
  environment$get_utterances <- function(...) returned
  environment$get_transcripts <- function(...) bind_rows(transcripts, mutate(transcripts[1, ], transcript_id = 9))
  Sys.setenv(CHILDES_CODE_DIR = source_dir)
  setwd(run_dir)
  source(file.path(source_dir, "childes_get_utterances.R"), local = environment)
  exported <- read.csv("data/childes_utterances.csv")
  stopifnot(nrow(exported) == 2L, identical(exported$full_utterance, c("you go", "I see it")))
  stopifnot(all(exported$participant_language == "eng"),
            all(exported$transcript_language == "eng"))
})
cat("PASS: export script retains exactly the selected speaker's utterance and its metadata\n")
