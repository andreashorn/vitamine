on run
	set launcherPath to "/Users/andreashorn/Library/CloudStorage/Dropbox-Personal/aiprojects/vitamine/scripts/open_vitamine_cloud_preview.sh"
	try
		do shell script "/bin/zsh " & quoted form of launcherPath
	on error errorMessage
		display alert "VitaMine Preview" message errorMessage as critical
	end try
end run
