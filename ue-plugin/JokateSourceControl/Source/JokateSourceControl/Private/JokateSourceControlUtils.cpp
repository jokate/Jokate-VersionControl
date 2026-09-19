// Jokate revision control plugin for Unreal Engine.

#include "JokateSourceControlUtils.h"

#include "Dom/JsonObject.h"
#include "Misc/Paths.h"

namespace JokateSourceControlUtils
{

FString NormalizeFilename(const FString& InFilename)
{
	FString Result = InFilename;
	FPaths::NormalizeFilename(Result);
	Result = FPaths::ConvertRelativePathToFull(Result);
	return Result;
}

FString ToContentRelative(const FString& InAbsoluteFilename)
{
	const FString Content = NormalizeFilename(FPaths::ProjectContentDir());
	FString Full = NormalizeFilename(InAbsoluteFilename);

	FString ContentWithSlash = Content;
	if (!ContentWithSlash.EndsWith(TEXT("/")))
	{
		ContentWithSlash += TEXT("/");
	}

	if (!Full.StartsWith(ContentWithSlash, ESearchCase::IgnoreCase))
	{
		return FString();
	}

	return Full.RightChop(ContentWithSlash.Len());
}

FString FromContentRelative(const FString& InRelativePath)
{
	return NormalizeFilename(FPaths::Combine(FPaths::ProjectContentDir(), InRelativePath));
}

EJokateFileState ParseState(const FString& InState)
{
	if (InState.Equals(TEXT("modified"), ESearchCase::IgnoreCase))
	{
		return EJokateFileState::Modified;
	}
	if (InState.Equals(TEXT("added"), ESearchCase::IgnoreCase) || InState.Equals(TEXT("new"), ESearchCase::IgnoreCase))
	{
		return EJokateFileState::Added;
	}
	if (InState.Equals(TEXT("deleted"), ESearchCase::IgnoreCase))
	{
		return EJokateFileState::Deleted;
	}
	if (InState.Equals(TEXT("missing"), ESearchCase::IgnoreCase))
	{
		return EJokateFileState::Missing;
	}
	if (InState.Equals(TEXT("untracked"), ESearchCase::IgnoreCase))
	{
		return EJokateFileState::Untracked;
	}
	if (InState.Equals(TEXT("same"), ESearchCase::IgnoreCase)
		|| InState.Equals(TEXT("clean"), ESearchCase::IgnoreCase)
		|| InState.Equals(TEXT("unchanged"), ESearchCase::IgnoreCase))
	{
		return EJokateFileState::Clean;
	}
	return EJokateFileState::Unknown;
}

bool ParseStatesResponse(const TSharedPtr<FJsonObject>& InJson, TMap<FString, EJokateFileState>& OutStates, FString& OutHeadFix)
{
	if (!InJson.IsValid())
	{
		return false;
	}

	InJson->TryGetStringField(TEXT("head_fix"), OutHeadFix);

	const TSharedPtr<FJsonObject>* StatesObject = nullptr;
	if (!InJson->TryGetObjectField(TEXT("states"), StatesObject) || StatesObject == nullptr || !StatesObject->IsValid())
	{
		return false;
	}

	for (const auto& Pair : (*StatesObject)->Values)
	{
		const TSharedPtr<FJsonObject>* Entry = nullptr;
		FString StateText;
		if (Pair.Value.IsValid() && Pair.Value->TryGetObject(Entry) && Entry != nullptr && Entry->IsValid())
		{
			(*Entry)->TryGetStringField(TEXT("state"), StateText);
		}
		else
		{
			Pair.Value->TryGetString(StateText);
		}
		OutStates.Add(Pair.Key, ParseState(StateText));
	}

	return true;
}

} // namespace JokateSourceControlUtils
