# Asana to Clubhouse

Asana to Clubhouse migration tool

## Setup

    $ pipenv install

## Usage

### Help

    $ pipenv run importer --help

### Preview

    $ pipenv run importer --asana-project-id XXXXX --clubhouse-project-id XXXX --asana-moved-tag-id XXXXXXX --clubhouse-complete-workflow-id XXXXXX

### Commit

    $ pipenv run importer ... --commit
