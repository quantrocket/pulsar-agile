'''Pulsar app for creating releases. Used by pulsar.
'''
import os
from datetime import date

from dateutil import parser

from pulsar.utils.importer import module_attribute
from pulsar.utils.html import capfirst

from ..utils import AgileApp, AgileError

close_issue = set((
    'close',
    'closes',
    'closed',
    'fix',
    'fixes',
    'fixed',
    'resolve',
    'resolves',
    'resolved'
))


class Github(AgileApp):
    description = 'Create a new release in github'

    async def __call__(self, name, config, options):
        git = self.git
        gitapi = self.gitapi
        release = {}
        opts = dict(options)
        opts.update(config)

        # Validate new tag and write the new version
        version = opts.get('version')
        if not version:
            raise AgileError('"version" not specified in github.%s dictionary'
                             % name)

        version = self.render(version)
        if opts.get('python_module'):
            self.logger.debug('Releasing a python module')
            version = module_attribute(version)

        tag_prefix = opts.get('tag_prefix', '')
        repo = gitapi.repo(git.repo_path)
        current_tag = await repo.validate_tag(version, tag_prefix)
        #
        # Release notes
        note_file = os.path.join(self.repo_path, "release-notes.md")
        if os.path.isfile(note_file):
            with open(note_file, 'r') as file:
                release['body'] = file.read().strip()
        else:
            self.logger.info('Create release notes from commits &'
                             'pull requests')
            release['body'] = await self.release_notes(repo, version,
                                                       current_tag)
            with open(note_file, 'w') as file:
                file.write(release['body'])
            self.logger.info('Created new %s file' % note_file)
        #
        if self.cfg.commit or self.cfg.push:
            #
            # Add release note to the changelog
            await self.cfg.write_notes(self.app, release)
            self.logger.info('Commit changes')
            result = await git.commit(msg='Release %s' % version)
            self.logger.info(result)
            if self.cfg.push:
                self.logger.info('Push changes changes')
                result = await git.push()
                self.logger.info(result)

                self.logger.info('Creating a new tag %s' % version)
                tag = await repo.create_tag(release)
                self.logger.info('Congratulation, the new release %s is out',
                                 tag)

        return True

    async def release_notes(self, repo, version, current_tag):
        """Fetch release notes from github
        """
        dt = date.today()
        dt = dt.strftime('%Y-%b-%d')
        created_at = current_tag['created_at']
        notes = []
        notes.extend(await self._from_commits(repo, created_at))
        notes.extend(await self._from_pull_requests(repo, created_at))

        sections = {}
        for _, section, body in reversed(sorted(notes, key=lambda s: s[0])):
            if section not in sections:
                sections[section] = []
            sections[section].append(body)

        body = ['# Ver. %s - %s' % (version, dt), '']
        for title in sorted(sections):
            if title:
                body.append('## %s' % capfirst(title))
            for entry in sections[title]:
                if not entry.startswith('* '):
                    entry = '* %s' % entry
                body.append(entry)
            body.append('')
        return '\n'.join(body)

    async def add_note(self, repo, notes, message, dte, eid, entry):
        """Add a not to the list of notes if a release note key is found
        """
        key = '#release-note'
        index = message.find(key)

        if index == -1:
            substitutes = {}
            bits = message.split()
            for msg, bit in zip(bits[:-1], bits[1:]):
                if bit.startswith('#') and msg.lower() in close_issue:
                    try:
                        number = int(bit[1:])
                    except ValueError:
                        continue
                    if bit not in substitutes:
                        try:
                            issue = await repo.issue(number).get()
                        except Exception:
                            continue
                        substitutes[bit] = issue['html_url']
            if substitutes:
                for name, url in substitutes.items():
                    message = message.replace(name, '[%s](%s)' % (name, url))
                notes.append((dte, '', message))
        else:
            index1 = index + len(key)
            if len(message) > index1 and message[index1] == '=':
                section = message[index1+1:].split()[0]
                key = '%s=%s' % (key, section)
            else:
                section = ''
            body = message.replace(key, '').strip()
            if body:
                body = capfirst(body)
                body = '%s [%s](%s)' % (body, eid, entry['html_url'])
                notes.append((dte, section.lower(), body))

    async def _from_commits(self, repo, created_at):
        #
        # Collect notes from commits
        commits = await repo.commits(since=created_at)
        notes = []
        for entry in commits:
            commit = entry['commit']
            dte = parser.parse(commit['committer']['date'])
            eid = entry['sha'][:7]
            message = commit['message']
            await self.add_note(repo, notes, message, dte, eid, entry)
            if commit['comment_count']:
                commit = repo.commit(entry['sha'])
                for comment in await commit.comments():
                    message = comment['body']
                    await self.add_note(repo, notes, message, dte, eid, entry)
        return notes

    async def _from_pull_requests(self, repo, created_at):
        #
        # Collect notes from commits
        pulls = await repo.pulls(callback=check_update(created_at),
                                 state='closed', sort='updated',
                                 direction='desc')
        notes = []
        for entry in pulls:
            message = entry['body']
            dte = parser.parse(entry['closed_at'])
            eid = '#%d' % entry['number']
            await self.add_note(repo, notes, message, dte, eid, entry)
            pull = repo.issue(entry['number'])
            for comment in await pull.comments():
                message = comment['body']
                await self.add_note(repo, notes, message, dte, eid, entry)
        return notes


class check_update:

    def __init__(self, since):
        self.since = parser.parse(since)

    def __call__(self, pulls):
        new_pulls = []
        for pull in pulls:
            dte = parser.parse(pull['updated_at'])
            if dte > self.since:
                new_pulls.append(pull)
        return new_pulls