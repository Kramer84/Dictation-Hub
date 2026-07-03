import json
from pathlib import Path
from typing import Union
from pydantic import BaseModel, Field, field_validator, ValidationError


class StorageConfig(BaseModel):
    """Configuration for directory and folder structures."""

    # We type this as a Path object rather than a string for better path manipulation downstream
    base_dir: Path = Field(
        ..., description="The root directory for saving transcriptions."
    )
    folder_format: str = Field(
        ..., description="Datetime format string for generating subfolders."
    )

    @field_validator("base_dir", mode="before")
    @classmethod
    def expand_home_directory(cls, value: Union[str, Path]) -> Path:
        """
        Automatically expands the '~' (tilde) to the absolute path
        of the current user's home directory.
        """
        return Path(value).expanduser()


class SuffixesConfig(BaseModel):
    """Configuration for file naming conventions and extensions."""

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
    """
    Root configuration schema representing the entire JSON file.
    Mirrors the exact structure of the configuration file.
    """

    storage: StorageConfig
    suffixes: SuffixesConfig

    @classmethod
    def load_from_file(cls, file_path: Union[str, Path]) -> "WhisperPipelineConfig":
        """
        Safely loads, parses, and validates the configuration from a JSON file.
        """
        path = Path(file_path)

        if not path.is_file():
            raise FileNotFoundError(
                f"Configuration file could not be found at: {path.absolute()}"
            )

        try:
            with path.open("r", encoding="utf-8") as file:
                config_dict = json.load(file)

            # model_validate ingests the dictionary and maps it to our nested classes
            return cls.model_validate(config_dict)

        except json.JSONDecodeError as e:
            raise ValueError(
                f"Failed to parse JSON file at {path}. Invalid JSON structure: {str(e)}"
            )
        except ValidationError as e:
            raise ValueError(
                f"Configuration validation failed. The JSON schema does not match the expected structure:\n{e}"
            )
