# -----------------------------------------------------------
# File: childes_get_adult_speakers.R
# Purpose: Extract ALL non-child English-speaking participants
#          from CHILDES and save to CSV.
# -----------------------------------------------------------

library(childesr)
library(dplyr)
source(file.path(Sys.getenv("CHILDES_CODE_DIR", "."), "childes_selection.R"))

# Create timestamp
timestamp <- format(Sys.time(), "%Y%m%d_%H%M%S")

cat("Downloading participant metadata from CHILDES...\n")

# 1. Get ALL participants in database
participants <- get_participants(collection = ENGLISH_COLLECTIONS, db_version = CHILDES_DB_VERSION)
if (is.null(participants) || !nrow(participants)) stop("Participant retrieval returned no data")
participants$db_version <- CHILDES_DB_VERSION
dir.create("audit", showWarnings = FALSE, recursive = TRUE)
write.csv(participants, "audit/participants.csv", row.names = FALSE)
cat("Total participants:", nrow(participants), "\n")

# 2. Keep only English-language speakers
participants_eng <- filter(participants, collection_name %in% ENGLISH_COLLECTIONS, language == "eng")
cat("English participants:", nrow(participants_eng), "\n")

# 3. Remove target_child speakers
#    (We want ALL other speakers for now)
non_target_child_speakers <- select_english_participants(participants_eng)

cat("All non-child English speakers:", nrow(non_target_child_speakers), "\n")

# 4. Preview data
print(head(select(non_target_child_speakers, name, code, role, corpus_name)))

# 5. Save to CSV with timestamp
dir.create("rdata/speakers", showWarnings = FALSE, recursive = TRUE)
outfile <- paste0("rdata/speakers/childes_adult_english_speakers_", timestamp, ".csv")
write.csv(non_target_child_speakers, outfile, row.names = FALSE)
cat("Saved file to:", outfile, "\n")
