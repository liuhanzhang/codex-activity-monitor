"""Shared app identity for source and standalone bundles."""


def release_version(metadata):
    version = metadata['project']['version']
    suffix = metadata.get('tool', {}).get('codex-activity-monitor', {}).get('version-suffix')
    return version + (f'-{suffix}' if suffix else '')


def bundle_info(metadata, *, distribution):
    settings = metadata['tool']['codex-activity-monitor']
    prerelease = settings.get('release-prerelease')
    if type(prerelease) is not bool:
        raise ValueError('Set release-prerelease explicitly; version names do not determine the release channel.')
    info = {
        'CFBundleExecutable': 'MonitorApp',
        'CFBundleIdentifier': 'com.liuhanzhang.codexactivitymonitor',
        'CFBundleName': 'Codex Activity Monitor',
        'CFBundleDisplayName': 'Codex Activity Monitor',
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': metadata['project']['version'],
        'CFBundleVersion': str(settings['build-number']),
        'CAMReleaseVersion': release_version(metadata),
        'CAMDistribution': distribution,
        'NSHumanReadableCopyright': 'Copyright © 2026 Hanzhang Liu',
        'LSMinimumSystemVersion': '14.0',
        'LSUIElement': True,
        'NSPrincipalClass': 'NSApplication',
        'CFBundleIconFile': 'AppIcon.icns',
    }
    if distribution == 'preview':
        info.update(CFBundleIdentifier=info['CFBundleIdentifier'] + '.preview',
                    CFBundleName='Codex Activity Monitor Preview',
                    CFBundleDisplayName='Codex Activity Monitor Preview')
    feed = 'appcast-prerelease.xml' if prerelease else 'appcast.xml'
    info.update(
        SUFeedURL=settings.get('update-feed-base', 'https://liuhanzhang.github.io/codex-activity-monitor/updates/').rstrip('/') + '/' + feed,
        CAMReleasePrerelease=prerelease,
        CAMUpdateChannel='prerelease' if prerelease else 'stable',
        SUPublicEDKey=settings.get('sparkle-public-key', ''),
        SUDefaultsDomain=info['CFBundleIdentifier'] + '.updates',
        SUEnableAutomaticChecks=distribution != 'preview',
        SUScheduledCheckInterval=86400,
        SUAutomaticallyUpdate=False,
        SUAllowsAutomaticUpdates=False,
        SUEnableSystemProfiling=False,
        SUShowReleaseNotes=False,
    )
    return info
