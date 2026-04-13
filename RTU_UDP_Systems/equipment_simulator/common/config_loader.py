"""
Configuration Loader Module
Version: 1.0.2
Description: Load device configuration from INI files
"""

import os
import configparser
import logging

logger = logging.getLogger(__name__)


class DeviceConfig:
    """Device configuration container"""
    def __init__(self):
        self.slave_id = 0
        self.installed = False
        self.device_number = 0
        self.device_type = 0
        self.protocol = ""
        self.model = 0
        self.mppt_count = 0
        self.string_count = 0
        self.iv_scan = False
        self.control = "NONE"
    
    def __repr__(self):
        return (f"DeviceConfig(slave_id={self.slave_id}, installed={self.installed}, "
                f"device_number={self.device_number}, device_type={self.device_type}, "
                f"protocol='{self.protocol}', model={self.model}, "
                f"mppt_count={self.mppt_count}, string_count={self.string_count}, "
                f"iv_scan={self.iv_scan}, control='{self.control}')")


class ConfigLoader:
    """Load and manage device configurations"""
    
    def __init__(self, config_dir=None):
        """Initialize config loader
        
        Args:
            config_dir: Directory containing config files. If None, uses ./config
        """
        if config_dir is None:
            # Try to find config directory relative to script location
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_dir = os.path.join(os.path.dirname(script_dir), 'config')
        
        self.config_dir = config_dir
        self.device_models = {}
        self.channel_devices = {}
        
        # Load device models
        self._load_device_models()
    
    def _load_device_models(self):
        """Load device models from device_models.ini"""
        models_file = os.path.join(self.config_dir, 'device_models.ini')
        
        if not os.path.exists(models_file):
            logger.warning(f"Device models file not found: {models_file}")
            return
        
        config = configparser.ConfigParser()
        config.read(models_file)
        
        self.device_models = {
            'device_types': {},
            'rtu_models': {},
            'inverter_models': {},
            'inverter_features': {},
            'inverter_protocols': {},
            'sensor_models': {},
            'meter_models': {},
            'relay_models': {}
        }
        
        # Load each section
        for section in config.sections():
            if section in self.device_models:
                for key, value in config.items(section):
                    try:
                        key_int = int(key)
                        self.device_models[section][key_int] = value
                    except ValueError:
                        self.device_models[section][key] = value
        
        logger.info(f"Loaded device models from {models_file}")
    
    def load_channel_config(self, channel_num):
        """Load device configuration for a specific RS485 channel
        
        Args:
            channel_num: Channel number (1, 2, etc.)
            
        Returns:
            dict: Dictionary of device_number -> DeviceConfig
        """
        config_file = os.path.join(self.config_dir, f'rs485_ch{channel_num}.ini')
        
        if not os.path.exists(config_file):
            logger.warning(f"Channel config file not found: {config_file}")
            return {}
        
        config = configparser.ConfigParser()
        config.read(config_file)
        
        devices = {}
        
        for section in config.sections():
            if not section.startswith('device_'):
                continue
            
            try:
                dev = DeviceConfig()
                
                # Required fields
                dev.slave_id = config.getint(section, 'slave_id')
                dev.installed = config.get(section, 'installed', fallback='NO').upper() == 'YES'
                dev.device_number = config.getint(section, 'device_number')
                dev.device_type = config.getint(section, 'device_type')
                
                # Optional fields
                dev.protocol = config.get(section, 'protocol', fallback='modbus')
                dev.model = config.getint(section, 'model', fallback=0)
                dev.mppt_count = config.getint(section, 'mppt_count', fallback=0)
                
                # String count: use specified value or default to mppt_count * 2
                if config.has_option(section, 'string_count'):
                    dev.string_count = config.getint(section, 'string_count')
                else:
                    dev.string_count = dev.mppt_count * 2 if dev.mppt_count > 0 else 0
                
                # Boolean iv_scan
                iv_scan_str = config.get(section, 'iv_scan', fallback='false')
                dev.iv_scan = iv_scan_str.lower() in ('true', 'yes', '1')
                
                # Control mode
                dev.control = config.get(section, 'control', fallback='NONE').upper()
                
                # Only add installed devices
                if dev.installed:
                    devices[dev.device_number] = dev
                    logger.debug(f"Loaded device config: {dev}")
                
            except Exception as e:
                logger.error(f"Error parsing section {section}: {e}")
                continue
        
        self.channel_devices[channel_num] = devices
        logger.info(f"Loaded {len(devices)} devices from channel {channel_num} config")
        
        return devices
    
    def get_device(self, channel_num, device_number):
        """Get device configuration
        
        Args:
            channel_num: Channel number
            device_number: Device number
            
        Returns:
            DeviceConfig or None
        """
        if channel_num not in self.channel_devices:
            self.load_channel_config(channel_num)
        
        return self.channel_devices.get(channel_num, {}).get(device_number)
    
    def get_device_by_slave_id(self, channel_num, slave_id):
        """Get device configuration by Modbus slave ID
        
        Args:
            channel_num: Channel number
            slave_id: Modbus slave ID
            
        Returns:
            DeviceConfig or None
        """
        if channel_num not in self.channel_devices:
            self.load_channel_config(channel_num)
        
        devices = self.channel_devices.get(channel_num, {})
        for dev in devices.values():
            if dev.slave_id == slave_id:
                return dev
        return None
    
    def get_installed_devices(self, channel_num, device_type=None):
        """Get list of installed devices
        
        Args:
            channel_num: Channel number
            device_type: Optional filter by device type
            
        Returns:
            list of DeviceConfig
        """
        if channel_num not in self.channel_devices:
            self.load_channel_config(channel_num)
        
        devices = self.channel_devices.get(channel_num, {})
        
        if device_type is not None:
            return [d for d in devices.values() if d.device_type == device_type]
        
        return list(devices.values())
    
    def get_inverters(self, channel_num):
        """Get list of installed inverters
        
        Args:
            channel_num: Channel number
            
        Returns:
            list of DeviceConfig (device_type == 1)
        """
        return self.get_installed_devices(channel_num, device_type=1)
    
    def get_model_name(self, device_type, model_id):
        """Get model name for a device type and model ID
        
        Args:
            device_type: Device type (0-4)
            model_id: Model ID
            
        Returns:
            Model name string or "Unknown"
        """
        model_section_map = {
            0: 'rtu_models',
            1: 'inverter_models',
            2: 'sensor_models',
            3: 'meter_models',
            4: 'relay_models'
        }
        
        section = model_section_map.get(device_type)
        if section is None:
            return f"Unknown(type={device_type})"
        
        models = self.device_models.get(section, {})
        return models.get(model_id, f"Unknown(model={model_id})")
    
    def get_inverter_features(self, model_id):
        """Get inverter features for a model
        
        Args:
            model_id: Inverter model ID
            
        Returns:
            dict with 'iv_scan' and 'kdn' boolean values
        """
        features = self.device_models.get('inverter_features', {}).get(model_id, "false, false")
        parts = [p.strip().lower() for p in features.split(',')]
        
        return {
            'iv_scan': parts[0] in ('true', 'yes', '1') if len(parts) > 0 else False,
            'kdn': parts[1] in ('true', 'yes', '1') if len(parts) > 1 else False
        }


# Singleton instance
_config_loader = None

def get_config_loader(config_dir=None):
    """Get singleton ConfigLoader instance"""
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader(config_dir)
    return _config_loader


# Test code
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    
    # Test loading
    loader = ConfigLoader()
    
    # Load channel 1
    devices = loader.load_channel_config(1)
    
    print("\n=== Installed Devices ===")
    for dev_num, dev in devices.items():
        print(f"  Device {dev_num}: {dev}")
    
    print("\n=== Inverters Only ===")
    inverters = loader.get_inverters(1)
    for inv in inverters:
        model_name = loader.get_model_name(1, inv.model)
        features = loader.get_inverter_features(inv.model)
        print(f"  Inverter {inv.device_number}: {model_name}")
        print(f"    Slave ID: {inv.slave_id}")
        print(f"    MPPT: {inv.mppt_count}, Strings: {inv.string_count}")
        print(f"    IV Scan: {inv.iv_scan}, Control: {inv.control}")
        print(f"    Features: {features}")
