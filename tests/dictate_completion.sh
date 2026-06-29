#!/bin/bash
# core/dictate_completion.sh

_dictate_completions() {
    local cur opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"

    # Resolve the actual path of the dictate command to find the config dynamically
    local dictate_bin="$(command -v dictate 2>/dev/null)"
    [[ -z "$dictate_bin" ]] && return 0
    
    # Use readlink/realpath to resolve the symlink created in ~/.local/bin
    local router_path="$(readlink -f "$dictate_bin" 2>/dev/null)"
    [[ -z "$router_path" ]] && return 0
    
    local repo_root="$(dirname "$(dirname "$router_path")")"
    local config_file="$repo_root/configs/pipeline_config.json"

    [[ ! -f "$config_file" || ! -x "$(command -v jq)" ]] && return 0

    if [[ "$cur" == -* ]]; then
        # If typing a flag, autocomplete from valid_arguments
        opts=$(jq -r '.valid_arguments[] | "--" + .' "$config_file" 2>/dev/null)
        COMPREPLY=( $(compgen -W "${opts}" -- "${cur}") )
    elif [[ "$COMP_CWORD" -eq 1 ]]; then
        # If typing the first argument, autocomplete from profiles
        opts=$(jq -r '.profiles | keys[]' "$config_file" 2>/dev/null)
        COMPREPLY=( $(compgen -W "${opts}" -- "${cur}") )
    fi
    
    return 0
}

complete -F _dictate_completions dictate