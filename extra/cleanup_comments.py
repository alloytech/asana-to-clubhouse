import argparse

import keyring

from clubhouse import ClubhouseClient


def delete_comment(start, end, message):
    global story_id, comment
    c = ClubhouseClient(keyring.get_password('external', 'clubhouse'))
    for story_id in range(start, end):
        print(f"Checking story {story_id}")
        comments = c.get('stories', story_id).get('comments')
        if not comments:
            continue
        for comment in comments:
            if comment['text'].startswith(message):
                print(f"Deleting comment {comment['id']} from story {story_id}")
                c.delete("stories", story_id, 'comments', comment['id'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="")
    parser.add_argument('start', help="First story id to check")
    parser.add_argument('end', help="Last story id to check")
    parser.add_argument('message',
                        help="Last story id to check",
                        default='The task moved to https://app.clubhouse.io/')
    args = parser.parse_args()

    delete_comment(args.start, args.end, args.message)
