# -----------------------------------------------------------
# File: childes_filter_speakers.R
# Purpose: Allow manual filtering of which speakers the child hears
#          before extracting utterances for parsing.
# -----------------------------------------------------------
library(dplyr)
source(file.path(Sys.getenv("CHILDES_CODE_DIR", "."), "childes_selection.R"))

# Create timestamp
timestamp <- format(Sys.time(), "%Y%m%d_%H%M%S")

# 1. Load the most recent master speaker list
cat("Loading most recent adult speakers file...\n")
input_file <- get_most_recent_file(
  "rdata/speakers",
  "childes_adult_english_speakers_.*\\.csv"
)
cat("Loading:", input_file, "\n")
df <- read.csv(input_file, colClasses = c(id = "character", corpus_id = "character", collection_id = "character", db_version = "character"))
check_speaker_version(df)
df <- select_english_participants(df)
cat("Loaded master list:", nrow(df), "non-child English speakers.\n\n")

# 2. Extract and print ALL roles (so you know what exists)
all_roles <- sort(unique(df$role))
cat("==== ALL ROLES IN DATA (copy these into the filter list) ====\n")
for (r in all_roles) cat(r, "\n")
cat("=============================================================\n\n")

# 3. *** MANUAL FILTERING LIST ***
roles_to_keep <- c(
  "Adult",
  "Caretaker",
  "Father",
  "Friend",
  "Grandfather",
  "Grandmother",
  "Investigator",
  "Mother",
  "Narrator",
  "Playmate",
  "Relative",
  "Sibling",
  "Sister",
  "Brother",
  "Teacher",
  "Unidentified",
  "Visitor",
  "Teenager",
  "Participant",
  "Girl",
  "Male",
  "Student",
  "Environment",
  "Doctor",
  "Target_Adult"
)

# 4. Filter the speaker table based on your list
filtered_speakers <- df[df$role %in% roles_to_keep, ]
cat("Speakers retained after filtering:", nrow(filtered_speakers), "\n")

# Collection identity and exact language replace the unreliable corpus-name test.
if (!nrow(filtered_speakers)) stop("No eligible speakers remain")

# 5. Save filtered speaker table with timestamp
dir.create("rdata/speakers", showWarnings = FALSE, recursive = TRUE)
outfile <- paste0("rdata/speakers/childes_filtered_speakers_", timestamp, ".csv")
write.csv(filtered_speakers, outfile, row.names = FALSE)
cat("Filtered speaker list saved to:", outfile, "\n")

cat("\nRole distribution in filtered data:\n")
print(table(filtered_speakers$role))
