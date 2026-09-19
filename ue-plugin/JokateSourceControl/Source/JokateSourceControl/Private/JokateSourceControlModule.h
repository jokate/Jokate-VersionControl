// Jokate revision control plugin for Unreal Engine.

#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleInterface.h"
#include "Modules/ModuleManager.h"
#include "JokateSourceControlProvider.h"
#include "JokateSourceControlSettings.h"

/** Jokate 를 리비전 컨트롤 프로바이더로 등록하는 에디터 모듈. */
class FJokateSourceControlModule : public IModuleInterface
{
public:
	// IModuleInterface
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
	virtual bool SupportsDynamicReloading() override { return true; }

	FJokateSourceControlSettings& AccessSettings() { return Settings; }
	const FJokateSourceControlSettings& AccessSettings() const { return Settings; }

	void SaveSettings();

	FJokateSourceControlProvider& GetProvider() { return Provider; }

	static FJokateSourceControlModule& Get()
	{
		return FModuleManager::LoadModuleChecked<FJokateSourceControlModule>("JokateSourceControl");
	}

	static bool IsLoaded()
	{
		return FModuleManager::Get().IsModuleLoaded("JokateSourceControl");
	}

private:
	/** Tools 메뉴에 'Jokate 리비전 컨트롤' 섹션을 등록한다(ToolMenus 준비 후 호출). */
	void RegisterMenus();

	FJokateSourceControlSettings Settings;
	FJokateSourceControlProvider Provider;
};
