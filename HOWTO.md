How to merge changes from upstream
=================================

In fgtk repo:

1. Fetch upstream commits with `git fetch upstream` (the `upstream` remote is https://github.com/mk-fg/fgtk)
2. Cherry pick relevant commits with `git cherry-pick <commit>`, resolving conflicts with `git mergetool --tool=p4` (P4Merge)
3. Generate patches with `git format-patch <commit>` (use the commit before the first cherry-pick commit)

In twitch_vod_fetch repo:

1. For each patch:
	* Change file name (**desktop/media/twitch_vod_fetch** -> **twitch_vod_fetch.py**)
	* Remove **README.rst** changes
	* Apply patch with `git am --whitespace=fix <patch>`
	* Apply changes to **README.md** manually and commit
	* Squash **README.md** commit onto `git am` commit with `git rebase -i`
2. Push the commits
