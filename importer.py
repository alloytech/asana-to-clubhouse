import argparse
import logging
import mimetypes
import sys
import tempfile
from os import path
from pprint import pformat
from typing import Dict, List, Union, TypeVar

import asana
import keyring
import requests
from binaryornot import check
from jinja2 import Template

from clubhouse import ClubhouseClient, ClubhouseFile, ClubhouseComment, ClubhouseTask, \
    ClubhouseStory, ClubhouseLabel, ClubhouseUser

logger = logging.getLogger('importer')

T = TypeVar('T')
AsanaTask = Dict
AsanaUser = Dict
description_template = Template("""
{{ notes|trim }}

> Imported from [Asana](https://app.asana.com/0/{{ projects[0].id }}/{{ id }}/f)
""")

comment_template = Template("""
{% if not user_found %}> Posted by: {{ created_by.name }} {% endif %}{% if
resource_subtype == 'comment_edited' %} (Edited){% endif %}
{% if task.level and task.level > 0 %}> Posted on: [{{ task.name|trim }}]({{ url }}) {% endif %}

{{ text|trim }}
""")


class Importer(object):
    def __init__(self, args):
        asana.Client.DEFAULT_OPTIONS['page_size'] = 100
        self.asana = asana.Client.access_token(
            args.asana_api_key or get_secret_from_keyring('asana'))

        self.asana_skip_moved_tag = args.asana_skip_moved_tag
        self.asana_project_id = args.asana_project_id
        self.asana_moved_tag_id = args.asana_moved_tag_id

        self.clubhouse = ClubhouseClient(
            args.clubhouse_api_key or get_secret_from_keyring('clubhouse'))

        self.clubhouse_project_id = args.clubhouse_project_id
        self.clubhouse_complete_workflow_id = args.clubhouse_complete_workflow_id

        self.commit = args.commit
        self.user_mapping = self.build_asana_to_clubhouse_user_mapping()

    def import_project(self):
        if self.commit:
            logger.info('Commit mode enabled. Story(ies) will be created and Tasks will modified.')
        else:
            logger.info(
                'Preview mode enabled. Story(ies) will be NOT created and Tasks will NOT modified.')

        for task in self.asana.tasks.find_by_project(self.asana_project_id):
            self.import_task(task)

    def build_asana_to_clubhouse_user_mapping(self) -> Dict[str, str]:
        workspaces_id = self.asana.users.me()['workspaces'][0]['id']
        asana_users = self.asana.users.find_by_workspace(workspaces_id, {"opt_fields": 'email'})
        asana_email_to_user_ids = {user['email']: user['id'] for user in asana_users}
        return {asana_email_to_user_ids.get(user['profile']['email_address']): user for user in
                self.clubhouse.get('members')}

    def import_task(self, thin_task: AsanaTask):
        task = self.asana.tasks.find_by_id(thin_task['id'])
        for tag in task['tags']:
            moved_tag = int(self.asana_moved_tag_id)
            if tag['id'] == moved_tag:
                message = "Task {id}: '{name}' already migrated " \
                          "because it is tagged with '{moved_tag}'"
                logger.info(message.format(moved_tag=moved_tag, **task))
                return

        subtasks = flatten(self.get_subtasks(task))
        files = self.import_files(task, subtasks)
        story = self.create_story(task, subtasks, files)
        logger.info(f"Story created at: {story['app_url']}")
        self.update_asana_task(task, story)

    def import_files(self, task: AsanaTask, subtasks: List[AsanaTask]) -> List[ClubhouseFile]:
        return flatten([self._import_files(t) for t in [task] + subtasks])

    def _import_files(self, task):
        if not self.commit:
            logging.debug("Skipping fetching and uploading files ...")
            return [{'id': "fake-guid"}]

        options = {'opt_fields': 'name,download_url'}
        created_files: List[ClubhouseFile] = []
        for attachment in self.asana.attachments.find_by_task(task['id'], options):
            filename = attachment['name']
            with tempfile.SpooledTemporaryFile(suffix=filename, max_size=10 * 1024 * 1024) as fp:
                logging.info(f"Fetching {filename} for {task['id']} ...")
                url = attachment['download_url']
                fp.write(requests.get(url).content)
                fp.seek(0)
                logging.info(f"Uploading {filename} ...")
                content_type, _ = mimetypes.guess_type(filename)
                text_plain = 'text/plain'
                if not content_type or content_type == text_plain:
                    if check.is_binary_string(fp.read(1024)):
                        content_type = 'application/octet-stream'
                    else:
                        content_type = text_plain

                fp.seek(0)
                payload = {'file': (filename, fp, content_type, {'content-type': content_type})}
                file = self.clubhouse.post("files", files=payload)
                created_files.append(file)

        return created_files

    def get_subtasks(self, thin_task: AsanaTask, level: int = 0) -> List[Union[AsanaTask, List]]:
        subtasks = [self.asana.tasks.find_by_id(thin_task['id']) for thin_task in
                    self.asana.tasks.subtasks(thin_task['id'])]
        if not subtasks:
            return []
        else:
            for task in subtasks:
                # Will be used to makes nice markdown bullet points if subtask of a subtask
                task['level'] = level
            return subtasks + [self.get_subtasks(subtask, level + 1) for subtask in subtasks]

    def build_comments(self, task: AsanaTask, subtasks: List[AsanaTask]) -> List[ClubhouseComment]:
        return flatten([self._build_comments(subtask) for subtask in [task] + subtasks])

    def _build_comments(self, task: AsanaTask) -> List[ClubhouseComment]:
        return [self.build_comment(task, comment)
                for comment in self.asana.stories.find_by_task(task['id'])
                if comment['type'] != 'system']

    def get_requestor(self, task):
        for story in self.asana.stories.find_by_task(task['id']):
            return self.convert_to_clubhouse_user_id(story['created_by'])

    def build_comment(self, task: AsanaTask, comment: Dict) -> ClubhouseComment:
        user_id = self.convert_to_clubhouse_user_id(comment['created_by'])
        text = comment_template.render(user_found=(user_id is not None),
                                       task=task,
                                       url=self.get_asana_url(task), **comment).strip()
        return cleanup_dict(
            {
                'author_id': user_id,
                'created_at': comment['created_at'],
                'external_id': self.get_asana_url(task),
                'text': text
            }
        )

    def build_external_ticket(self, task: Dict) -> Dict:
        return {
            'external_url': self.get_asana_url(task),
            'external_id': str(task['id'])
        }

    def build_task(self, subtask: AsanaTask) -> ClubhouseTask:
        # Used to makes nice markdown bullet points if subtask of a subtask
        prefix = '' if not subtask['level'] else ' * '
        url = self.get_asana_url(subtask)
        return cleanup_dict({
            "description": f"{prefix}[{subtask['name']}]({url})",
            'complete': subtask['completed'],
            'created_at': subtask['created_at'],
            'external_id': self.get_asana_url(subtask),
            'owner_ids': self.get_owners(subtask)
        })

    @staticmethod
    def build_label_from_projects(project):
        return {
            'name': project['name'],
            'external_id': f"https://app.asana.com/0/{project['id']}"
        }

    @staticmethod
    def get_deadline(task: AsanaTask):
        if not task['due_on']:
            return None
        # Ensuring that the right day since the due_on is a date without datetime.
        return f"{task['due_on']}T23:59:59Z"

    def create_story(self, task: AsanaTask, subtasks: List[AsanaTask], files) -> Union[
        ClubhouseStory, None]:
        labels = [{'name': 'Asana'}]
        labels.extend([{'name': label['name']} for label in task['tags']])
        labels.extend([self.build_label_from_projects(project) for project in task['projects']])
        labels.extend(self.build_labels_from_custom_fields(task))
        tasks = [cleanup_dict(self.build_task(subtask)) for subtask in subtasks]
        workflow_id = self.clubhouse_complete_workflow_id if task['completed'] else None
        task_url = self.get_asana_url(task)
        external_tickets = [self.build_external_ticket(task)]
        completed_at = task['completed_at']
        story = cleanup_dict({
            'archived': True if completed_at else False,
            'comments': self.build_comments(task, subtasks),
            'completed_at_override': completed_at,
            'created_at': task['created_at'],
            'deadline': self.get_deadline(task),
            'story_type': self.get_story_type(task),
            'description': description_template.render(**task).strip(),
            'external_id': task_url,
            'labels': labels,
            'file_ids': [file['id'] for file in files],
            'follower_ids': self.get_follower_ids(task),
            'name': task['name'].strip(),
            'owner_ids': self.get_owners(task),
            'project_id': self.clubhouse_project_id,
            'requested_by_id': self.get_requestor(task),
            'tasks': tasks,
            'external_tickets': external_tickets,
            'updated_at': task['modified_at'],
            'workflow_state_id': workflow_id
        })

        logger.info(pformat(story))

        if not self.commit:
            logger.debug("Skipping creating story ...")
            return None

        response_story: ClubhouseStory = self.clubhouse.post('stories', json=story)
        return response_story

    def get_owners(self, task):
        user_id = self.convert_to_clubhouse_user_id(task['assignee'])
        if not user_id:
            return []
        return [user_id]

    @staticmethod
    def build_labels_from_custom_fields(task: AsanaTask) -> List[ClubhouseLabel]:
        return [
            {'name': custom_field['enum_value']['name']}
            for custom_field in task['custom_fields']
            if custom_field and custom_field.get('enum_value')
        ]

    @staticmethod
    def get_story_type(task: AsanaTask) -> str:
        feature = 'feature'
        bug = 'bug'
        for project in task['projects']:
            project_name = project['name'].lower().strip()
            if project_name == bug:
                return bug

        for field in task['custom_fields']:
            field_name = field['name'].lower().strip()
            if field_name == 'type' and field['enum_value']:
                enum = field['enum_value']['name'].lower().strip()
                if enum == bug:
                    return bug
                if enum == feature:
                    return feature
        return 'chore'

    def convert_to_clubhouse_user_id(self, user: AsanaUser) -> Union[str, None]:
        if not user:
            return None
        user: ClubhouseUser = self.user_mapping.get(user['id'])
        if not user:
            email = user.get('email', 'unknown') if user else 'unknown'
            logger.warning(f"The asana user '{email}' does not exist in clubhouse.")
            return None
        return user.get('id')

    def get_follower_ids(self, task: AsanaTask) -> List[str]:
        return cleanup_list([self.convert_to_clubhouse_user_id(user) for user in task['followers']])

    def get_asana_url(self, task: AsanaTask) -> str:
        return f"https://app.asana.com/0/{self.asana_project_id}/{task['id']}/f"

    # Include when ready
    def update_asana_task(self, task: AsanaTask, story: ClubhouseStory) -> None:
        if not self.commit or self.asana_skip_moved_tag:
            logger.debug("Skipping updating asana task ...")
            return

        self.asana.tasks.add_comment(task['id'], {
            'text': f"The task moved to {story['app_url']}"
        })
        self.asana.tasks.add_tag(task['id'], {'tag': self.asana_moved_tag_id})


def cleanup_dict(kv: Dict) -> Dict:
    return {k: v for k, v in kv.items() if v}


def cleanup_list(l: List) -> List:
    return [i for i in l if i]


def get_secret_from_keyring(service: str) -> str:
    return keyring.get_password('alloy', service)


def flatten(container: List[Union[List, T]]) -> List[T]:
    return list(_flatten(container))


def _flatten(container: List[Union[T, List]]) -> List[T]:
    for i in container:
        if isinstance(i, (list, tuple)):
            for j in _flatten(i):
                yield j
        else:
            yield i


def _setup_logging():
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="")
    parser.add_argument('--asana-api-key',
                        default='')
    parser.add_argument('--asana-project-id',
                        help='Source project.')
    parser.add_argument('--asana-moved-tag-id',
                        help='Tag to apply to moved tasks.')
    parser.add_argument('--asana-skip-moved-tag',
                        help='Do not tag the task at the end.',
                        action='store_true')
    parser.add_argument('--clubhouse-api-key',
                        default='')
    parser.add_argument('--clubhouse-project-id',
                        help='Destination project.')
    parser.add_argument('--clubhouse-complete-workflow-id',
                        help='Workflow ID to mark completed stories.')
    parser.add_argument('--commit',
                        default=False,
                        help='Changes things. Be careful!',
                        action='store_true')

    _setup_logging()
    Importer(parser.parse_args()).import_project()
