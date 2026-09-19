// Jokate revision control plugin for Unreal Engine.

#include "JokateSourceControlSettings.h"

#include "JokateSourceControlLog.h"
#include "Dom/JsonObject.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SourceControlHelpers.h"

namespace
{
	const TCHAR* SettingsSection = TEXT("JokateSourceControl.JokateSourceControlSettings");
}

int32 FJokateSourceControlSettings::GetConfiguredPort() const
{
	FScopeLock ScopeLock(&CriticalSection);
	return Port;
}

int32 FJokateSourceControlSettings::GetPort() const
{
	const int32 DaemonPort = ReadDaemonPort();
	if (DaemonPort > 0)
	{
		return DaemonPort;
	}
	return GetConfiguredPort();
}

void FJokateSourceControlSettings::SetPort(int32 InPort)
{
	FScopeLock ScopeLock(&CriticalSection);
	Port = (InPort > 0 && InPort < 65536) ? InPort : DefaultPort;
}

void FJokateSourceControlSettings::LoadSettings()
{
	const FString& IniFile = SourceControlHelpers::GetSettingsIni();
	int32 LoadedPort = DefaultPort;
	if (GConfig->GetInt(SettingsSection, TEXT("Port"), LoadedPort, IniFile))
	{
		SetPort(LoadedPort);
	}
}

void FJokateSourceControlSettings::SaveSettings() const
{
	const FString& IniFile = SourceControlHelpers::GetSettingsIni();
	const int32 PortToSave = GetConfiguredPort();
	GConfig->SetInt(SettingsSection, TEXT("Port"), PortToSave, IniFile);
}

FString FJokateSourceControlSettings::GetBaseUrl() const
{
	return FString::Printf(TEXT("http://127.0.0.1:%d"), GetPort());
}

int32 FJokateSourceControlSettings::ReadDaemonPort()
{
	const FString StateFile = FPaths::Combine(FPaths::ProjectDir(), TEXT(".jokate"), TEXT("daemon.json"));
	FString Text;
	if (!FFileHelper::LoadFileToString(Text, *StateFile))
	{
		return 0;
	}

	TSharedPtr<FJsonObject> Root;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
	if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
	{
		UE_LOG(LogJokateSourceControl, Warning, TEXT("daemon.json 을 읽지 못했습니다: %s"), *StateFile);
		return 0;
	}

	int32 DaemonPort = 0;
	if (Root->TryGetNumberField(TEXT("port"), DaemonPort) && DaemonPort > 0)
	{
		return DaemonPort;
	}
	return 0;
}
