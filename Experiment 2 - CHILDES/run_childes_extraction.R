# Usage: Rscript run_childes_extraction.R /absolute/path/to/new-run-directory
# Set R_LIBS_USER to a library containing childesr >= 0.3.0 and dplyr >= 1.1.0.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("Usage: Rscript run_childes_extraction.R NEW_OUTPUT_DIRECTORY")
script_arg <- grep("^--file=", commandArgs(), value = TRUE)[[1]]
code_dir <- dirname(normalizePath(sub("^--file=", "", script_arg)))
output <- path.expand(args[[1]])
if (file.exists(output)) stop("Output directory already exists: ", output)
library(childesr)
library(dplyr)
if (packageVersion("childesr") < "0.3.0") stop("Install childesr >= 0.3.0 for Redivis access")
if (packageVersion("dplyr") < "1.1.0") stop("Install dplyr >= 1.1.0")
if (!nzchar(Sys.getenv("REDIVIS_API_TOKEN"))) {
  token_file <- path.expand(Sys.getenv("REDIVIS_TOKEN_FILE", "~/redivis.token"))
  if (!file.exists(token_file)) stop("Set REDIVIS_API_TOKEN or provide REDIVIS_TOKEN_FILE")
  Sys.setenv(REDIVIS_API_TOKEN = trimws(paste(readLines(token_file, warn = FALSE), collapse = "")))
}
options(browser = function(...) stop("Redivis authentication requires a valid token"))
dir.create(output, recursive = TRUE)
output <- normalizePath(output)
dir.create(file.path(output, "code"))
scripts <- c("childes_selection.R", "childes_get_adult_speakers.R", "childes_filter_speakers.R",
             "childes_get_utterances.R", "run_childes_extraction.R",
             "analyze_complete_dataset_96mos.py", "analyze_age_groups_96mos.py")
if (!all(file.copy(file.path(code_dir, scripts), file.path(output, "code")))) stop("Cannot snapshot source code")
Sys.setenv(CHILDES_CODE_DIR = file.path(output, "code"))
setwd(output)
dir.create("audit")
source(file.path("code", "childes_selection.R"))
writeLines(capture.output(sessionInfo()), "audit/sessionInfo.txt")
write.csv(data.frame(db_version = CHILDES_DB_VERSION, collection_name = ENGLISH_COLLECTIONS,
                     transcript_language = "eng", participant_language = "eng"),
          "audit/selection_policy.csv", row.names = FALSE)
for (script in scripts[2:4]) {
  cat("RUNNING", script, "\n")
  source(file.path("code", script))
}
writeLines("complete", "audit/extraction_status.txt")
