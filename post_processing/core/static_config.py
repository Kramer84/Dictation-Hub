import json
import logging
from pathlib import Path
from typing import Union

from pydantic import BaseModel, Field, ValidationError, field_validator

# 1. Initialize module-level logger
# This will automatically inherit the handlers and level from the root logger configured in engine.py
logger = logging.getLogger(__name__)


class StorageConfig(BaseModel):
    base_dir: Path = Field(
        ..., description="The root directory for saving transcriptions."
    )
    folder_format: str = Field(
        ..., description="Datetime format string for generating subfolders."
    )

    @field_validator("base_dir", mode="before")
    @classmethod
    def expand_home_directory(cls, value: Union[str, Path]) -> Path:
        logger.debug("Expanding home directory for base_dir. Original value: '%s'", value)
        expanded_path = Path(value).expanduser()
        logger.debug("Expanded base_dir to: '%s'", expanded_path)
        return expanded_path


class SuffixesConfig(BaseModel):
    audio: str = Field(..., description="Suffix for raw audio files.")
    full_json: str = Field(
        ..., description="Suffix for the complete Whisper JSON payload."
    )
    cleaned_json: str = Field(
        ..., description="Suffix for the deterministic cleaned JSON output."
    )
    cleaned_md: str = Field(
        ..., description="Suffix for the markdown representation of cleaned segments."
    )
    raw_text: str = Field(
        ..., description="Suffix for the flattened string footprint of the dictation."
    )
    final_text: str = Field(..., description="Suffix for the final, polished text.")


class WhisperPipelineConfig(BaseModel):
    storage: StorageConfig
    suffixes: SuffixesConfig

    @classmethod
    def load_from_file(cls, file_path: Union[str, Path]) -> "WhisperPipelineConfig":
        path = Path(file_path)
        logger.info("Attempting to load WhisperPipelineConfig from: '%s'", path.absolute())

        if not path.is_file():
            error_msg = f"Configuration file could not be found at: {path.absolute()}"
            logger.error("File not found: '%s'", path.absolute())
            raise FileNotFoundError(error_msg)

        try:
            logger.debug("Opening configuration file: '%s'", path)
            with path.open("r", encoding="utf-8") as file:
                config_dict = json.load(file)
            logger.debug("Successfully read and decoded JSON from: '%s'", path)

            logger.debug("Validating configuration dictionary against Pydantic schema...")
            validated_model = cls.model_validate(config_dict)
            logger.info("Successfully loaded and validated WhisperPipelineConfig.")
            return validated_model

        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse JSON file at {path}. Invalid JSON structure: {str(e)}"
            # logger.exception automatically logs at ERROR level and appends the traceback
            logger.exception("JSON decode error encountered while reading: '%s'", path)
            raise ValueError(error_msg) from e
            
        except ValidationError as e:
            error_msg = f"Configuration validation failed. The JSON schema does not match the expected structure:\n{e}"
            logger.exception("Pydantic validation error for configuration payload from: '%s'", path)
            raise ValueError(error_msg) from e