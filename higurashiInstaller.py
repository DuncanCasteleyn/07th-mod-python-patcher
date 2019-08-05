from __future__ import unicode_literals

import json
import traceback

import commandLineParser
import common
import os, os.path as path, shutil, subprocess, glob, stat

########################################## Installer Functions  and Classes ############################################
import fileVersionManagement
import gameScanner
import installConfiguration
import logger


def on_rm_error(func, path, exc_info):
	# path contains the path of the file that couldn't be removed
	# let's just assume that it's read-only and unlink it.
	os.chmod(path, stat.S_IWRITE)
	os.unlink(path)

# Remove a file, even if it's marked as readonly
def forceRemove(path):
	os.chmod(path, stat.S_IWRITE)
	os.remove(path)

def forceRemoveDir(path):
	os.chmod(path, stat.S_IWRITE)
	os.rmdir(path)

# Call shutil.rmtree, such that it even removes readonly files
def forceRmTree(path):
	shutil.rmtree(path, onerror=on_rm_error)

class Installer:
	def __init__(self, fullInstallConfiguration, extractDirectlyToGameDirectory):
		# type: (installConfiguration.FullInstallConfiguration, bool) -> None

		"""
		Installer Init

		:param str directory: The directory of the game
		:param dict info: The info dictionary from server JSON file for the requested target
		"""
		self.info = fullInstallConfiguration
		self.directory = fullInstallConfiguration.installPath

		if common.Globals.IS_MAC:
			self.dataDirectory = path.join(self.directory, "Contents/Resources/Data")
		else:
			self.dataDirectory = path.join(self.directory, self.info.subModConfig.dataName)

		logger.getGlobalLogger().trySetSecondaryLoggingPath(
			os.path.join(self.dataDirectory, common.Globals.LOG_BASENAME)
		)

		self.assetsDir = path.join(self.dataDirectory, "StreamingAssets")

		possibleSteamPaths = [
			path.join(self.directory, "steam_api.dll"),
			path.join(self.directory, "Contents/Plugins/CSteamworks.bundle"),
			path.join(self.directory, "libsteam_api.so")
		]

		self.isSteam = False
		for possibleSteamPath in possibleSteamPaths:
			if path.exists(possibleSteamPath):
				self.isSteam = True

		self.downloadDir = self.info.subModConfig.modName + " Downloads"
		self.extractDir = self.directory if extractDirectlyToGameDirectory else (self.info.subModConfig.modName + " Extraction")

		self.fileVersionManager = fileVersionManagement.VersionManager(
			subMod=self.info.subModConfig,
			modFileList=self.info.buildFileListSorted(datadir=self.dataDirectory),
			localVersionFolder=self.directory)

		modFileList = self.fileVersionManager.getFilesRequiringUpdate()
		self.downloaderAndExtractor = common.DownloaderAndExtractor(modFileList=modFileList,
		                                                            downloadTempDir=self.downloadDir,
		                                                            extractionDir=self.extractDir)

		self.downloaderAndExtractor.buildDownloadAndExtractionList()

		parser = installConfiguration.ModOptionParser(self.info)

		for opt in parser.downloadAndExtractOptionsByPriority:
			self.downloaderAndExtractor.addItemManually(
				url=opt.url,
				extractionDir=os.path.join(self.extractDir, opt.relativeExtractionPath),
			)

		self.downloaderAndExtractor.printPreview()

	def backupUI(self):
		"""
		Backs up the `sharedassets0.assets` file
		"""
		uiPath = path.join(self.dataDirectory, "sharedassets0.assets")
		backupPath = path.join(self.dataDirectory, "sharedassets0.assets.backup")
		if path.exists(uiPath) and not path.exists(backupPath):
			os.rename(uiPath, backupPath)

	def cleanOld(self):
		"""
		Removes folders that shouldn't persist through the install
		(CompiledUpdateScripts, CG, and CGAlt)
		"""
		oldCG = path.join(self.assetsDir, "CG")
		oldCGAlt = path.join(self.assetsDir, "CGAlt")
		compiledScriptsPattern = path.join(self.assetsDir, "CompiledUpdateScripts/*.mg")

		try:
			for mg in glob.glob(compiledScriptsPattern):
				forceRemove(mg)
		except Exception:
			print('WARNING: Failed to clean up the [{}] compiledScripts'.format(compiledScriptsPattern))
			traceback.print_exc()

		# Only delete the oldCG and oldCGAlt folders on a full update, as the CG pack won't always be extracted
		if self.fileVersionManager.fullUpdateRequired():
			print("Full Update Detected: Deleting old CG and CGAlt folders")
			try:
				if path.isdir(oldCG):
					forceRmTree(oldCG)
			except Exception:
				print('WARNING: Failed to clean up the [{}] directory'.format(oldCG))
				traceback.print_exc()

			try:
				if path.isdir(oldCGAlt):
					forceRmTree(oldCGAlt)
			except Exception:
				print('WARNING: Failed to clean up the [{}] directory'.format(oldCGAlt))
				traceback.print_exc()
		else:
			print("Not cleaning oldCG/oldCGAlt as performing Partial Update")

	def download(self):
		self.downloaderAndExtractor.download()

	def extractFiles(self):
		self.downloaderAndExtractor.extract()

	def moveFilesIntoPlace(self):
		"""
		Moves files from the directory they were extracted to
		to the game data folder
		"""
		self._moveDirectoryIntoPlace(
			fromDir = os.path.join(self.extractDir, self.info.subModConfig.dataName),
			toDir = self.dataDirectory
		)
		if common.Globals.IS_WINDOWS:
			exePath = self.info.subModConfig.dataName[:-5] + ".exe"
			self._moveFileIntoPlace(
				fromPath = os.path.join(self.extractDir, exePath),
				toPath = os.path.join(self.directory, exePath),
			)
		elif common.Globals.IS_MAC:
			self._moveFileIntoPlace(
				fromPath = os.path.join(self.extractDir, "Contents/Resources/PlayerIcon.icns"),
				toPath = os.path.join(self.directory, "Contents/Resources/PlayerIcon.icns")
			)


	def _moveDirectoryIntoPlace(self, fromDir, toDir):
		# type: (str, str) -> None
		"""
		Recursive function that does the actual moving for `moveFilesIntoPlace`
		"""
		for file in os.listdir(fromDir):
			src = path.join(fromDir, file)
			target = path.join(toDir, file)
			if path.isdir(src):
				if not path.exists(target):
					os.mkdir(target)
				self._moveDirectoryIntoPlace(fromDir=src, toDir=target)
			else:
				if path.exists(target):
					forceRemove(target)
				shutil.move(src, target)
		forceRemoveDir(fromDir)

	def _moveFileIntoPlace(self, fromPath, toPath):
		# type: (str, str) -> None
		"""
		Moves a single file from `fromPath` to `toPath`
		"""
		if path.exists(fromPath):
			if path.exists(toPath):
				forceRemove(toPath)
			shutil.move(fromPath, toPath)

	def cleanup(self, cleanExtractionDirectory):
		"""
		General cleanup and other post-install things

		Removes downloaded 7z files
		On mac, modifies the application Info.plist with new values if available
		"""
		try:
			forceRmTree(self.downloadDir)
			if cleanExtractionDirectory:
				forceRmTree(self.extractDir)
		except OSError:
			pass

		if common.Globals.IS_MAC:
			# Allows fixing up application Info.plist file so that the titlebar doesn't show `Higurashi01` as the name of the application
			# Can also add a custom CFBundleIdentifier to change the save directory (e.g. for Console Arcs)
			infoPlist = path.join(self.directory, "Contents/Info.plist")
			infoPlistJSON = subprocess.check_output(["plutil", "-convert", "json", "-o", "-", infoPlist])
			parsed = json.loads(infoPlistJSON)

			configCFBundleName = self.info.subModConfig.CFBundleName
			if configCFBundleName and parsed["CFBundleName"] != configCFBundleName:
				subprocess.call(["plutil", "-replace", "CFBundleName", "-string", configCFBundleName, infoPlist])

			configCFBundleIdentifier = self.info.subModConfig.CFBundleIdentifier
			if configCFBundleIdentifier and parsed["CFBundleIdentifier"] != configCFBundleIdentifier:
				subprocess.call(["plutil", "-replace", "CFBundleIdentifier", "-string", configCFBundleIdentifier, infoPlist])

	def saveFileVersionInfoStarted(self):
		self.fileVersionManager.saveVersionInstallStarted()

	def saveFileVersionInfoFinished(self):
		self.fileVersionManager.saveVersionInstallFinished()

def main(fullInstallConfiguration):
	# type: (installConfiguration.FullInstallConfiguration) -> None

	isVoiceOnly = fullInstallConfiguration.subModConfig.subModName == 'voice-only'
	if isVoiceOnly:
		print("Performing Voice-Only Install - backupUI() and cleanOld() will NOT be performed.")

	# On Windows, extract directly to the game directory to avoid path-length issues and speed up install
	if common.Globals.IS_WINDOWS:
		installer = Installer(fullInstallConfiguration, extractDirectlyToGameDirectory=True)
		print("Downloading...")
		installer.download()
		installer.saveFileVersionInfoStarted()
		if not isVoiceOnly:
			installer.backupUI()
			installer.cleanOld()
		print("Extracting...")
		installer.extractFiles()
		commandLineParser.printSeventhModStatusUpdate(97, "Cleaning up...")
		installer.saveFileVersionInfoFinished()
		installer.cleanup(cleanExtractionDirectory=False)
	else:
		installer = Installer(fullInstallConfiguration, extractDirectlyToGameDirectory=False)
		print("Downloading...")
		installer.download()
		installer.saveFileVersionInfoStarted()
		print("Extracting...")
		installer.extractFiles()
		commandLineParser.printSeventhModStatusUpdate(85, "Moving files into place...")
		if not isVoiceOnly:
			installer.backupUI()
			installer.cleanOld()
		installer.moveFilesIntoPlace()
		commandLineParser.printSeventhModStatusUpdate(97, "Cleaning up...")
		installer.saveFileVersionInfoFinished()
		installer.cleanup(cleanExtractionDirectory=True)

	commandLineParser.printSeventhModStatusUpdate(100, "Install Completed!")
