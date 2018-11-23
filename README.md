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

## Notes

- For best results, if you want to make a task with children be an epic you
  should first convert the task to project so the nesting, comments, etc. are
  preserved.
