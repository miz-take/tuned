import os, copy
import tuned.plugins
import tuned.logs
import tuned.monitors
import struct

log = tuned.logs.get()

class DiskPlugin(tuned.plugins.Plugin):

	_supported_vendors = ["ATA", "SCSI"]

	def __init__(self, devices, options):
		"""
		"""
		super(self.__class__, self).__init__(None, options)

		self.devidle = {}
		self.stats = {}
		self.power = ["255", "225", "195", "165", "145", "125", "105", "85", "70", "55", "30", "20"]
		self.spindown = ["0", "250", "230", "210", "190", "170", "150", "130", "110", "90", "70", "60"]
		self.levels = len(self.power)
		self._elevator_set = False
		self._old_elevator = ""

		if not tuned.utils.storage.Storage.get_instance().data.has_key("disk"):
			tuned.utils.storage.Storage.get_instance().data["disk"] = {}
		self._load_monitor = tuned.monitors.get_repository().create("disk", devices)

	@classmethod
	def tunable_devices(cls):
		block_devices = os.listdir("/sys/block")
		available = set(filter(cls._is_device_supported, block_devices))
		cls._available_devices = available

	@classmethod
	def _is_device_supported(cls, device):
		vendor_file = "/sys/block/%s/device/vendor" % device
		try:
			vendor = open(vendor_file).read().strip()
		except IOError:
			return False

		return vendor in cls._supported_vendors

	@classmethod
	def _get_default_options(cls):
		return {
			"elevator"   : "",
		}

	def _apply_elevator(self, dev):
		storage = tuned.utils.storage.Storage.get_instance()
		if storage.data["disk"].has_key(dev):
			self._old_elevator = storage.data["disk"][dev]
			self._revert_elevator(dev)
			del storage.data["disk"][dev]

		if len(self._options["elevator"]) == 0:
			return False

		try:
			f = open(os.path.join("/sys/block/", dev, "queue/scheduler"), "r")
			self._old_elevator = f.read()
			f.close()
		except (OSError,IOError) as e:
			log.error("Getting elevator of %s error: %s" % (dev, e))

		storage = tuned.utils.storage.Storage.get_instance()
		storage.data["disk"] = {dev : self._old_elevator}
		storage.save()

		log.debug("Applying elevator: %s < %s" % (dev, self._options["elevator"]))
		try:
			f = open(os.path.join("/sys/block/", dev, "queue/scheduler"), "w")
			f.write(self._options["elevator"])
			f.close()
		except (OSError,IOError) as e:
			log.error("Setting elevator on %s error: %s" % (dev, e))
		return True

	def _revert_elevator(self, dev):
		if len(self._old_elevator) == 0:
			return

		log.debug("Applying elevator: %s < %s" % (dev, self._old_elevator))
		try:
			f = open(os.path.join("/sys/block/", dev, "queue/scheduler"), "w")
			f.write(self._old_elevator)
			f.close()
		except (OSError,IOError) as e:
			log.error("Setting elevator on %s error: %s" % (dev, e))

	def _update_idle(self, dev):
		idle = self.devidle.setdefault(dev, {})
		idle.setdefault("LEVEL", 0)
		for type in ("read", "write"):
			if self.stats[dev][type] == 0.0:
				idle.setdefault(type, 0)
				idle[type] += 1
			else:
				idle.setdefault(type, 0)
				idle[type] = 0

	def _init_stats(self, dev):
		if not self.stats.has_key(dev):
			self.stats[dev] = {}
			self.stats[dev]["new"] = ['0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0']
			self.stats[dev]["old"] = ['0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0']
			self.stats[dev]["max"] = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]

	def _calc_diff(self, dev):
		l = []
		for i in xrange(len(self.stats[dev]["old"])):
			l.append(int(self.stats[dev]["new"][i]) - int(self.stats[dev]["old"][i]))
		return l

	def _update_stats(self, dev, devload):
		self.stats[dev]["old"] = self.stats[dev]["new"]
		self.stats[dev]["new"] = devload
		l = self._calc_diff(dev)
		for i in xrange(len(l)):
			if l[i] > self.stats[dev]["max"][i]:
				self.stats[dev]["max"][i] = l[i]

		self.stats[dev]["diff"] = l
	
		self.stats[dev]["read"] = float(self.stats[dev]["diff"][1]) / float(self.stats[dev]["max"][1])
		self.stats[dev]["write"] = float(self.stats[dev]["diff"][5]) / float(self.stats[dev]["max"][5])

	def cleanup(self):
		log.debug("Cleanup")

		for dev in self.devidle.keys():
			if self.devidle[dev]["LEVEL"] > 0:
				os.system("hdparm -S0 -B255 /dev/"+dev+" > /dev/null 2>&1")
			self._revert_elevator(dev)

	def update_tuning(self):
		load = self._load_monitor.get_load()
		for dev, devload in load.iteritems():
			if not self._elevator_set:
				self._apply_elevator(dev)

			self._init_stats(dev)
			self._update_stats(dev, devload)
			self._update_idle(dev)

			if self.devidle[dev]["LEVEL"] < self.levels-1 and self.devidle[dev]["read"] >= 6 and self.devidle[dev]["write"] >= 6:
				self.devidle[dev].setdefault("LEVEL", 0)
				self.devidle[dev]["LEVEL"] += 1
				level = self.devidle[dev]["LEVEL"]

				log.debug("Level changed to %d (power %s, spindown %s)" % (level, self.power[level], self.spindown[level]))
				os.system("hdparm -S"+self.power[level]+" -B"+self.spindown[level]+" /dev/"+dev+" > /dev/null 2>&1")

			if self.devidle[dev]["LEVEL"] > 0 and (self.devidle[dev]["read"] == 0 or self.devidle[dev]["write"] == 0):
				self.devidle[dev].setdefault("LEVEL", 0)
				self.devidle[dev]["LEVEL"] -= 2
				if self.devidle[dev]["LEVEL"] < 0:
					self.devidle[dev]["LEVEL"] = 0
				level = self.devidle[dev]["LEVEL"]

				log.debug("Level changed to %d (power %s, spindown %s)" % (level, self.power[level], self.spindown[level]))
				os.system("hdparm -S"+self.power[level]+" -B"+self.spindown[level]+" /dev/"+dev+" > /dev/null 2>&1")

			log.debug("%s load: read %f, write %f" % (dev, self.stats[dev]["read"], self.stats[dev]["write"]))
			log.debug("%s idle: read %d, write %d, level %d" % (dev, self.devidle[dev]["read"], self.devidle[dev]["write"], self.devidle[dev]["LEVEL"]))

		self._elevator_set = True